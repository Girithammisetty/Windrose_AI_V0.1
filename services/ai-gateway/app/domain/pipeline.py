"""The data-plane enforcement pipeline (BRD 12 §7, normative order):

request → authN (key + JWT) → attribution validation → admission (streams/RPM/TPM)
        → guardrails-in (PII redact ∥ injection classify) → semantic cache lookup
        → budget pre-flight (reserve, stacked windows, top-down)
        → ladder resolve (class → rung → cloud-affinity deployment)
        → provider call (retry/failover) → guardrails-out (schema; de-redaction)
        → cache write → budget settle → metering event → response

A rejection at any stage short-circuits later stages and stamps
`windrose.rejected_stage` on the span (BR-1 ordering holds: guardrail block
happens before any budget reservation or provider call)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from app.config import Settings
from app.domain.admission import AdmissionController
from app.domain.budgets import BudgetEngine, Preflight
from app.domain.cache import SemanticCache, context_hash_of, prompt_hash_of
from app.domain.entities import (
    Attribution,
    PipelineResult,
    ProviderDeployment,
    RequestLog,
    Rung,
    VirtualKey,
)
from app.domain.errors import (
    AppError,
    KeyInvalid,
    PermissionDenied,
    UpstreamUnavailable,
    ValidationFailed,
)
from app.domain.guardrails import GuardrailEngine, GuardrailOutcome
from app.domain.ladders import LadderService
from app.domain.ports import (
    ProviderClient,
    ProviderError,
    ProviderRequest,
    ProviderResult,
    Span,
    Tracer,
    UowFactory,
)
from app.domain.pricing import PriceTable
from app.domain.reconciliation import UsageRecorder
from app.domain.routing import AttemptPlan, CircuitBreaker, Router, backoff_ms
from app.events.envelope import make_envelope
from app.utils import Clock, estimate_tokens


@dataclass
class RequestCtx:
    request_id: str
    tenant_id: str
    principal_sub: str
    principal_typ: str
    key: VirtualKey
    request_class: str
    attribution: Attribution
    cell_cloud: str | None
    trace_id: str | None
    escalate: bool = False
    prior_request_id: str | None = None
    min_rung: int | None = None
    actor: dict = field(default_factory=dict)
    via_agent: dict | None = None


async def _default_sleeper(ms: int) -> None:
    await asyncio.sleep(ms / 1000)


class GatewayService:
    def __init__(self, *, uow_factory: UowFactory, settings: Settings, clock: Clock,
                 ladders: LadderService, budgets: BudgetEngine,
                 guardrails: GuardrailEngine, cache: SemanticCache,
                 admission: AdmissionController, router: Router,
                 breaker: CircuitBreaker, provider: ProviderClient,
                 prices: PriceTable, tracer: Tracer, metrics, usage_recorder: UsageRecorder,
                 anomaly=None, sleeper=None, spend_guard=None):
        self.uow_factory = uow_factory
        self.settings = settings
        self.clock = clock
        self.ladders = ladders
        self.budgets = budgets
        self.guardrails = guardrails
        self.cache = cache
        self.admission = admission
        self.router = router
        self.breaker = breaker
        self.provider = provider
        self.prices = prices
        self.tracer = tracer
        self.metrics = metrics
        self.usage_recorder = usage_recorder
        self.anomaly = anomaly
        self.sleeper = sleeper or _default_sleeper
        # Spend kill-switch (P2): instant operator freeze, checked before any spend.
        self.spend_guard = spend_guard

    # ================================================================== chat

    async def chat(self, ctx: RequestCtx, body: dict) -> PipelineResult:
        span = self.tracer.start_span("chat")
        span.set_attribute("windrose.tenant_id", ctx.tenant_id)
        span.set_attribute("windrose.request_class", ctx.request_class)
        span.set_attribute("windrose.price_version", self.prices.version)
        started = self.clock.now()
        stream = bool(body.get("stream"))
        stream_acquired = False
        try:
            # Spend kill-switch (P2): reject instantly if a platform/tenant freeze is
            # active — before admission or any provider spend.
            if self.spend_guard is not None:
                try:
                    await self.spend_guard.check(ctx.tenant_id)
                except AppError:
                    span.set_attribute("windrose.rejected_stage", "spend_freeze")
                    raise
            messages = self._validate_chat_body(ctx, body, span)
            prompt_tokens_est = sum(
                estimate_tokens(m["content"]) for m in messages
                if isinstance(m.get("content"), str)
            )

            # ---- admission -------------------------------------------------
            try:
                await self.admission.check_rpm_tpm(ctx.tenant_id, prompt_tokens_est)
                if stream:
                    await self.admission.acquire_stream(ctx.tenant_id)
                    stream_acquired = True
            except AppError:
                span.set_attribute("windrose.rejected_stage", "admission")
                raise

            # ---- guardrails-in ---------------------------------------------
            policy = await self.guardrails.policy_for(ctx.tenant_id)
            try:
                guard = await self.guardrails.inbound(
                    ctx.tenant_id, messages, policy, ctx.request_id
                )
            except AppError as exc:
                span.set_attribute("windrose.rejected_stage", "guardrails_in")
                await self._emit_guardrail_events(ctx, [
                    self.guardrails._event(
                        exc.details.get("kind", "unknown") if exc.details else "unknown",
                        "block", "blocked", policy.version, ctx.request_id,
                    )
                ])
                raise
            if guard.events:
                await self._emit_guardrail_events(ctx, guard.events)
            if guard.flags:
                span.set_attribute("windrose.guardrail_flags", list(guard.flags))
            messages = guard.messages

            # ---- ladder + degrade state (read-only) -------------------------
            ladder = await self.ladders.resolve(ctx.tenant_id, ctx.request_class)
            tz_name, tenant_ttl = await self._tenant_prefs(ctx.tenant_id)
            windows = await self.budgets.governing_windows(
                ctx.tenant_id, ctx.attribution, ctx.principal_sub, ctx.key.id,
                ctx.request_class, tz_name,
            )
            degrading = False
            if ctx.request_class != "judge":  # judge is never degraded
                from app.domain.errors import DependencyUnavailable
                from app.domain.ports import LedgerUnavailable

                try:
                    for gw in windows:
                        spent, _ = await self.budgets.ledger.usage(gw.ledger_key)
                        if gw.budget.limit_cents and spent >= (
                            gw.budget.limit_cents * gw.budget.degrade_pct / 100
                        ):
                            degrading = True
                            break
                except LedgerUnavailable as exc:  # BR-14: never fail-open
                    span.set_attribute("windrose.rejected_stage", "budget_preflight")
                    raise DependencyUnavailable(
                        "budget ledger unavailable; failing closed (BR-14)"
                    ) from exc

            escalate_from = await self._escalate_from(ctx, span)
            try:
                rung_idx, escalated = self.ladders.select_rung(
                    ladder,
                    requested_model=body.get("model", "windrose-auto"),
                    min_rung=ctx.min_rung,
                    escalate_from=escalate_from,
                    key_max_rung=ctx.key.max_rung,
                    degraded=degrading,
                )
            except AppError:
                span.set_attribute("windrose.rejected_stage", "ladder")
                raise
            rung = ladder.rung(rung_idx)
            temperature = self._temperature(ctx, body, rung)
            max_tokens = min(int(body.get("max_tokens") or rung.max_tokens), rung.max_tokens)
            span.set_attribute("windrose.rung", rung_idx)
            span.set_attribute("windrose.escalated", escalated)
            if escalated:
                span.set_attribute("windrose.escalation_reason", "explicit_request")
            if degrading:
                span.set_attribute("windrose.degraded", "budget")

            # ---- semantic cache lookup ---------------------------------------
            ttl = self.cache.ttl_for(tenant_ttl)
            p_hash = prompt_hash_of(messages)
            c_hash = context_hash_of(
                model_alias=rung.model_alias,
                request_class=ctx.request_class,
                tools=body.get("tools"),
                temperature=temperature,
                system_prompt_version=body.get("system_prompt_version"),
                guardrail_policy_version=policy.version,
            )
            cacheable = self.cache.eligible(
                request_class=ctx.request_class, temperature=temperature,
                stream=stream, tools=body.get("tools"), ttl_seconds=ttl,
            )
            cache_tier = "skip"
            if cacheable:
                cache_tier, cached_response = await self.cache.lookup(
                    ctx.tenant_id, messages, p_hash, c_hash
                )
                if cache_tier.startswith("hit"):
                    span.set_attribute("windrose.cache", cache_tier)
                    span.set_attribute("windrose.budget_state",
                                       "degrading" if degrading else "ok")
                    self.metrics.inc("aig_cache_hits_total", tenant=ctx.tenant_id)
                    result = self._rewrap_cached(ctx, cached_response)
                    await self._record_and_meter(
                        ctx, span, started, rung_idx, rung.model_alias, None,
                        input_tokens=0, output_tokens=0, cost_usd=0.0, cached=True,
                        guardrail_flags=guard.flags, degraded=degrading, status="ok",
                    )
                    replay = None
                    if stream:
                        replay = self._replay_cached_as_sse(ctx, rung.model_alias,
                                                            result, stream_acquired)
                    elif stream_acquired:
                        await self.admission.release_stream(ctx.tenant_id)
                    return PipelineResult(
                        request_id=ctx.request_id,
                        response=None if stream else result, rung=rung_idx,
                        model_alias=rung.model_alias, deployment_id=None,
                        cache=cache_tier, degraded=degrading, escalated=escalated,
                        guardrail_flags=guard.flags, stream=replay,
                    )
            span.set_attribute("windrose.cache", cache_tier if cacheable else "skip")

            # ---- budget pre-flight -------------------------------------------
            quote = self.prices.quote(rung.model_alias)
            estimate = quote.cost_cents(prompt_tokens_est, max_tokens)
            try:
                preflight = await self.budgets.preflight(windows, estimate)
            except AppError:
                span.set_attribute("windrose.rejected_stage", "budget_preflight")
                span.set_attribute("windrose.budget_state", "exhausted")
                raise
            span.set_attribute("windrose.budget_state", preflight.governing_state)

            # ---- provider call (+ failover, schema validation, escalation) ---
            try:
                if stream:
                    return await self._serve_stream(
                        ctx, body, span, started, ladder, rung_idx, rung, temperature,
                        max_tokens, messages, guard, preflight, quote, degrading,
                        escalated, cacheable, p_hash, c_hash, ttl,
                    )
                return await self._serve_non_stream(
                    ctx, body, span, started, ladder, rung_idx, rung, temperature,
                    max_tokens, messages, guard, preflight, quote, degrading,
                    escalated, cacheable, p_hash, c_hash, ttl, policy,
                )
            except AppError:
                await self.budgets.release(preflight)
                raise
        except AppError as exc:
            if stream_acquired and not isinstance(exc, asyncio.CancelledError):
                await self.admission.release_stream(ctx.tenant_id)
            span.set_attribute("windrose.error_code", exc.code)
            await self._log_request(
                ctx, started, rung=-1, model_alias="", deployment_id=None,
                input_tokens=0, output_tokens=0, cost_usd=0.0, cached=False,
                guardrail_flags=[], status=exc.code,
            )
            raise

    # ---------------------------------------------------------------- non-stream

    async def _serve_non_stream(self, ctx, body, span: Span, started, ladder,
                                rung_idx, rung: Rung, temperature, max_tokens,
                                messages, guard: GuardrailOutcome,
                                preflight: Preflight, quote, degrading, escalated,
                                cacheable, p_hash, c_hash, ttl, policy) -> PipelineResult:
        response_format = body.get("response_format")
        schema_on = policy.policy.get("schema_validation", "on") == "on"
        deployments = await self._active_deployments()

        current_idx, current_rung, current_quote = rung_idx, rung, quote
        schema_retry_done = False
        schema_escalated = False
        while True:
            result, deployment = await self._call_with_failover(
                ctx, span, deployments, current_rung, messages, temperature,
                min(max_tokens, current_rung.max_tokens), body, stream=False,
            )
            schema_error = None
            if schema_on:
                schema_error = self.guardrails.validate_output_schema(
                    result.content, response_format
                )
            if schema_error is None:
                break
            # AIG-FR-052: retry once at the same rung, then escalate one rung.
            if not schema_retry_done:
                schema_retry_done = True
                span.add_event("schema_invalid_retry", {"rung": current_idx})
                continue
            if not schema_escalated and current_idx < ladder.top_rung and (
                current_idx < ctx.key.max_rung
            ) and (
                ladder.max_rung is None or current_idx < ladder.max_rung
            ) and not degrading:
                next_idx = current_idx + 1
                next_rung = ladder.rung(next_idx)
                next_quote = self.prices.quote(next_rung.model_alias)
                prompt_est = sum(estimate_tokens(m["content"]) for m in messages
                                 if isinstance(m.get("content"), str))
                extra = max(0, next_quote.cost_cents(prompt_est, max_tokens)
                            - preflight.estimate_cents)
                await self.budgets.reserve_more(preflight, extra)
                span.add_event("schema_invalid_escalate",
                               {"from_rung": current_idx, "to_rung": next_idx})
                span.set_attribute("windrose.escalated", True)
                span.set_attribute("windrose.escalation_reason", "schema_invalid")
                current_idx, current_rung, current_quote = next_idx, next_rung, next_quote
                escalated = True
                schema_escalated = True  # AIG-FR-052: escalate one rung at most
                continue
            await self._settle_and_meter(
                ctx, span, started, preflight, current_quote, result,
                current_idx, current_rung.model_alias, deployment, guard,
                degrading, status="OUTPUT_SCHEMA_INVALID",
            )
            from app.domain.errors import OutputSchemaInvalid

            span.set_attribute("windrose.rejected_stage", "guardrails_out")
            raise OutputSchemaInvalid(f"provider output failed schema validation: "
                                      f"{schema_error}")

        # ---- guardrails-out: de-redaction ---------------------------------
        content = result.content
        if guard.redaction_map and guard.deredact_response:
            content = self.guardrails.deredact(content, guard.redaction_map)
        response = self._chat_response(ctx, current_rung.model_alias, content, result)

        # ---- cache write (BR-6: only clean 2xx schema-valid responses) -----
        if cacheable and not guard.flags:
            await self.cache.store(ctx.tenant_id, messages, p_hash, c_hash,
                                   response, ttl, ctx.attribution.workspace_id)

        await self._settle_and_meter(
            ctx, span, started, preflight, current_quote, result, current_idx,
            current_rung.model_alias, deployment, guard, degrading, status="ok",
        )
        span.set_attribute("windrose.rung", current_idx)
        return PipelineResult(
            request_id=ctx.request_id, response=response, rung=current_idx,
            model_alias=current_rung.model_alias, deployment_id=deployment.id,
            cache="miss" if cacheable else "skip", degraded=degrading,
            escalated=escalated, guardrail_flags=guard.flags,
        )

    # ---------------------------------------------------------------- streaming

    async def _serve_stream(self, ctx, body, span: Span, started, ladder, rung_idx,
                            rung: Rung, temperature, max_tokens, messages,
                            guard: GuardrailOutcome, preflight: Preflight, quote,
                            degrading, escalated, cacheable, p_hash, c_hash,
                            ttl) -> PipelineResult:
        deployments = await self._active_deployments()
        candidates, plan = self._plan(span, deployments, rung, ctx.cell_cloud, ladder,
                                      rung_idx)
        service = self

        async def sse():
            deployment: ProviderDeployment | None = None
            content_parts: list[str] = []
            usage = {"input_tokens": 0, "output_tokens": 0}
            first_token_ms: int | None = None
            streamed_any = False
            try:
                last_error: Exception | None = None
                prev: ProviderDeployment | None = None
                for dep in plan.sequence:
                    if prev is dep:  # same-deployment retry → jittered backoff
                        await service.sleeper(backoff_ms(service.settings))
                    prev = dep
                    preq = service._provider_request(
                        dep, messages, temperature, max_tokens, body, stream=True
                    )
                    try:
                        agen = service.provider.stream(dep, preq)
                        async for chunk in agen:
                            if not streamed_any:
                                first_token_ms = int(
                                    (service.clock.now() - started).total_seconds() * 1000
                                )
                                streamed_any = True
                                deployment = dep
                            if "usage" in chunk:
                                usage = chunk["usage"]
                                yield service._sse_usage_chunk(ctx, rung.model_alias, usage)
                                continue
                            delta = chunk.get("delta", "")
                            if guard.redaction_map and guard.deredact_response:
                                delta = service.guardrails.deredact(
                                    delta, guard.redaction_map
                                )
                            content_parts.append(delta)
                            yield service._sse_delta_chunk(ctx, rung.model_alias, delta)
                        if streamed_any:
                            service.breaker.record(dep.id, True)
                            break
                    except (ProviderError, TimeoutError) as exc:
                        service.breaker.record(dep.id, False)
                        span.add_event("provider_attempt_failed",
                                       {"deployment": dep.id, "error": str(exc)})
                        last_error = exc
                        if streamed_any:
                            # never retry after bytes reached the client (AIG-FR-008)
                            yield service._sse_error_chunk("UPSTREAM_UNAVAILABLE")
                            break
                        continue
                if not streamed_any:
                    await service.budgets.release(preflight)
                    yield service._sse_error_chunk("UPSTREAM_UNAVAILABLE")
                    yield "data: [DONE]\n\n"
                    await service._log_request(
                        ctx, started, rung=rung_idx, model_alias=rung.model_alias,
                        deployment_id=None, input_tokens=0, output_tokens=0,
                        cost_usd=0.0, cached=False, guardrail_flags=guard.flags,
                        status="UPSTREAM_UNAVAILABLE",
                    )
                    _ = last_error
                    return
                yield "data: [DONE]\n\n"
                # ---- post-stream: cache write, settle, meter (BR-2) --------
                if cacheable and not guard.flags and not body.get("tools"):
                    response = service._chat_response(
                        ctx, rung.model_alias, "".join(content_parts),
                        ProviderResult(
                            content="".join(content_parts),
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                            model=rung.model_alias,
                        ),
                    )
                    await service.cache.store(ctx.tenant_id, messages, p_hash, c_hash,
                                              response, ttl,
                                              ctx.attribution.workspace_id)
                result = ProviderResult(
                    content="".join(content_parts),
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    model=rung.model_alias,
                )
                await service._settle_and_meter(
                    ctx, span, started, preflight, quote, result, rung_idx,
                    rung.model_alias, deployment, guard, degrading, status="ok",
                    first_token_ms=first_token_ms,
                )
            finally:
                await service.admission.release_stream(ctx.tenant_id)

        return PipelineResult(
            request_id=ctx.request_id, response=None, rung=rung_idx,
            model_alias=rung.model_alias,
            deployment_id=candidates.deployments[0].id if candidates.deployments else None,
            cache="miss" if cacheable else "skip", degraded=degrading,
            escalated=escalated, guardrail_flags=guard.flags, stream=sse(),
        )

    # ---------------------------------------------------------------- embeddings

    async def embeddings(self, ctx: RequestCtx, body: dict) -> PipelineResult:
        span = self.tracer.start_span("embeddings")
        span.set_attribute("windrose.tenant_id", ctx.tenant_id)
        span.set_attribute("windrose.request_class", ctx.request_class)
        span.set_attribute("windrose.price_version", self.prices.version)
        started = self.clock.now()
        try:
            if self.spend_guard is not None:
                try:
                    await self.spend_guard.check(ctx.tenant_id)
                except AppError:
                    span.set_attribute("windrose.rejected_stage", "spend_freeze")
                    raise
            inputs = body.get("input")
            if isinstance(inputs, str):
                inputs = [inputs]
            if not isinstance(inputs, list) or not inputs:
                raise ValidationFailed("input must be a non-empty string or array")
            if len(inputs) > self.settings.embed_batch_max_inputs:
                raise ValidationFailed(
                    f"embedding batch exceeds {self.settings.embed_batch_max_inputs} inputs"
                )
            self._check_key_class(ctx)
            tokens = sum(estimate_tokens(t) for t in inputs)
            try:
                await self.admission.check_rpm_tpm(ctx.tenant_id, tokens)
            except AppError:
                span.set_attribute("windrose.rejected_stage", "admission")
                raise

            ladder = await self.ladders.resolve(ctx.tenant_id, "embed")
            rung = ladder.rung(0)
            span.set_attribute("windrose.rung", 0)
            span.set_attribute("windrose.cache", "skip")
            tz_name, _ = await self._tenant_prefs(ctx.tenant_id)
            windows = await self.budgets.governing_windows(
                ctx.tenant_id, ctx.attribution, ctx.principal_sub, ctx.key.id,
                ctx.request_class, tz_name,
            )
            quote = self.prices.quote(rung.model_alias)
            # BR-17: the batch is budget-reserved as one unit
            try:
                preflight = await self.budgets.preflight(windows,
                                                         quote.cost_cents(tokens, 0))
            except AppError:
                span.set_attribute("windrose.rejected_stage", "budget_preflight")
                span.set_attribute("windrose.budget_state", "exhausted")
                raise
            span.set_attribute("windrose.budget_state", preflight.governing_state)

            deployments = await self._active_deployments()
            candidates, plan = self._plan(span, deployments, rung, ctx.cell_cloud,
                                          ladder, 0)
            try:
                vectors, deployment, input_tokens = None, None, 0
                last_exc: Exception | None = None
                prev = None
                for dep in plan.sequence:
                    if prev is dep:
                        await self.sleeper(backoff_ms(self.settings))
                    prev = dep
                    try:
                        vectors, input_tokens = await self.provider.embed(
                            dep, dep.deployment_name, inputs
                        )
                        self.breaker.record(dep.id, True)
                        deployment = dep
                        break
                    except (ProviderError, TimeoutError) as exc:
                        self.breaker.record(dep.id, False)
                        last_exc = exc
                if deployment is None:
                    # BR-17: partial provider failure fails the whole batch
                    raise UpstreamUnavailable(
                        f"no embedding deployment available: {last_exc}"
                    )
            except AppError:
                await self.budgets.release(preflight)
                span.set_attribute("windrose.rejected_stage", "provider")
                raise

            result = ProviderResult(content="", input_tokens=input_tokens,
                                    output_tokens=0, model=rung.model_alias)
            await self._settle_and_meter(
                ctx, span, started, preflight, quote, result, 0, rung.model_alias,
                deployment, GuardrailOutcome(messages=[]), False, status="ok",
            )
            response = {
                "object": "list",
                "model": rung.model_alias,
                "data": [
                    {"object": "embedding", "index": i, "embedding": v}
                    for i, v in enumerate(vectors)
                ],
                "usage": {"prompt_tokens": input_tokens, "total_tokens": input_tokens},
            }
            return PipelineResult(
                request_id=ctx.request_id, response=response, rung=0,
                model_alias=rung.model_alias, deployment_id=deployment.id,
                cache="skip", degraded=False, escalated=False,
            )
        except AppError as exc:
            span.set_attribute("windrose.error_code", exc.code)
            raise

    # ---------------------------------------------------------------- internals

    def _validate_chat_body(self, ctx: RequestCtx, body: dict, span: Span) -> list[dict]:
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            span.set_attribute("windrose.rejected_stage", "attribution")
            raise ValidationFailed("messages must be a non-empty array")
        self._check_key_class(ctx, span)
        return messages

    def _check_key_class(self, ctx: RequestCtx, span: Span | None = None) -> None:
        if ctx.key.tenant_id != ctx.tenant_id:
            raise KeyInvalid("virtual key does not belong to the caller's tenant")
        if ctx.request_class not in ctx.key.allowed_request_classes:
            if span:
                span.set_attribute("windrose.rejected_stage", "attribution")
            raise PermissionDenied(
                f"virtual key does not allow request class {ctx.request_class!r}"
            )

    def _temperature(self, ctx: RequestCtx, body: dict, rung: Rung) -> float:
        if ctx.request_class == "judge":
            return 0.0  # AIG-FR temperature forced to 0 for judge
        t = body.get("temperature")
        return float(t) if t is not None else rung.temperature_default

    async def _escalate_from(self, ctx: RequestCtx, span: Span) -> int | None:
        if not ctx.escalate:
            return None
        if not ctx.prior_request_id:
            span.set_attribute("windrose.rejected_stage", "ladder")
            raise ValidationFailed(
                "x-windrose-escalate requires x-windrose-prior-request-id"
            )
        async with self.uow_factory(ctx.tenant_id) as uow:
            prior = await uow.request_log.get(ctx.prior_request_id)
        if prior is None:
            span.set_attribute("windrose.rejected_stage", "ladder")
            raise ValidationFailed("prior request_id not found for escalation")
        return prior.rung

    async def _tenant_prefs(self, tenant_id: str) -> tuple[str, int | None]:
        async with self.uow_factory(tenant_id) as uow:
            cfg = await uow.tenant_configs.get(tenant_id)
        if cfg is None:
            return "UTC", None
        return cfg.timezone or "UTC", cfg.cache_ttl_seconds

    async def _active_deployments(self) -> list[ProviderDeployment]:
        async with self.uow_factory(self.settings.platform_tenant_id) as uow:
            return await uow.providers.list_all_active_or_draining()

    def _plan(self, span: Span, deployments, rung: Rung, cell_cloud, ladder,
              rung_idx: int):
        """Candidate resolution with BR-8 rung-up fallback."""
        idx = rung_idx
        current = rung
        while True:
            candidates = self.router.candidates(deployments, current.model_alias,
                                                cell_cloud)
            if candidates.deployments:
                if idx != rung_idx:
                    span.set_attribute("windrose.routing.rung_fallback", "up")
                if candidates.cross_cloud:
                    span.set_attribute("windrose.routing.cross_cloud", True)
                span.set_attribute("windrose.routing.candidates",
                                   list(candidates.evaluation_order))
                return candidates, AttemptPlan(candidates.deployments)
            if idx >= ladder.top_rung:
                span.set_attribute("windrose.rejected_stage", "provider")
                raise UpstreamUnavailable(
                    f"no active deployment serves any rung of the "
                    f"{ladder.request_class} ladder"
                )
            idx += 1
            current = ladder.rung(idx)

    def _provider_request(self, deployment: ProviderDeployment, messages, temperature,
                          max_tokens, body, stream: bool) -> ProviderRequest:
        return ProviderRequest(
            model=deployment.deployment_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
            response_format=body.get("response_format"),
            tools=body.get("tools"),
            extra={"stream_options": {"include_usage": True}} if stream else {},
        )

    async def _call_with_failover(self, ctx, span: Span, deployments, rung: Rung,
                                  messages, temperature, max_tokens, body,
                                  stream: bool):
        ladder = await self.ladders.resolve(ctx.tenant_id, ctx.request_class)
        rung_idx = next(
            (i for i, r in enumerate(ladder.rungs) if r["model_alias"] == rung.model_alias),
            0,
        )
        candidates, plan = self._plan(span, deployments, rung, ctx.cell_cloud,
                                      ladder, rung_idx)
        last_exc: Exception | None = None
        prev: ProviderDeployment | None = None
        attempts = []
        for dep in plan.sequence:
            if prev is dep:
                await self.sleeper(backoff_ms(self.settings))
            prev = dep
            preq = self._provider_request(dep, messages, temperature, max_tokens,
                                          body, stream)
            try:
                async with asyncio.timeout(self.settings.total_timeout_s):
                    result = await self.provider.complete(dep, preq)
                self.breaker.record(dep.id, True)
                attempts.append({"deployment": dep.id, "outcome": "ok"})
                span.set_attribute("windrose.routing.attempts", attempts)
                span.set_attribute("windrose.deployment", dep.id)
                span.set_attribute("gen_ai.request.model", preq.model)
                span.set_attribute("gen_ai.response.model", result.model)
                return result, dep
            except (ProviderError, TimeoutError) as exc:
                self.breaker.record(dep.id, False)
                attempts.append({"deployment": dep.id, "outcome": "error",
                                 "error": str(exc)})
                last_exc = exc
        span.set_attribute("windrose.routing.attempts", attempts)
        span.set_attribute("windrose.rejected_stage", "provider")
        raise UpstreamUnavailable(f"all provider attempts failed: {last_exc}")

    def _chat_response(self, ctx: RequestCtx, model_alias: str, content: str,
                       result: ProviderResult) -> dict:
        return {
            "id": f"chatcmpl-{ctx.request_id}",
            "object": "chat.completion",
            "created": int(self.clock.now().timestamp()),
            "model": model_alias,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": result.finish_reason,
            }],
            "usage": {
                "prompt_tokens": result.input_tokens,
                "completion_tokens": result.output_tokens,
                "total_tokens": result.input_tokens + result.output_tokens,
            },
        }

    def _rewrap_cached(self, ctx: RequestCtx, cached: dict) -> dict:
        out = dict(cached)
        out["id"] = f"chatcmpl-{ctx.request_id}"
        return out

    def _replay_cached_as_sse(self, ctx: RequestCtx, model_alias: str,
                              cached: dict, stream_acquired: bool):
        """A cache hit on a streaming request is replayed as SSE chunks."""

        async def replay():
            try:
                content = cached["choices"][0]["message"]["content"]
                usage = cached.get("usage") or {}
                yield self._sse_delta_chunk(ctx, model_alias, content)
                yield self._sse_usage_chunk(ctx, model_alias, {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                })
                yield "data: [DONE]\n\n"
            finally:
                if stream_acquired:
                    await self.admission.release_stream(ctx.tenant_id)

        return replay()

    def _sse_delta_chunk(self, ctx, model_alias: str, delta: str) -> str:
        return "data: " + json.dumps({
            "id": f"chatcmpl-{ctx.request_id}",
            "object": "chat.completion.chunk",
            "model": model_alias,
            "choices": [{"index": 0, "delta": {"content": delta},
                         "finish_reason": None}],
        }) + "\n\n"

    def _sse_usage_chunk(self, ctx, model_alias: str, usage: dict) -> str:
        return "data: " + json.dumps({
            "id": f"chatcmpl-{ctx.request_id}",
            "object": "chat.completion.chunk",
            "model": model_alias,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0)
                + usage.get("output_tokens", 0),
            },
        }) + "\n\n"

    def _sse_error_chunk(self, code: str) -> str:
        return "data: " + json.dumps({"error": {"code": code}}) + "\n\n"

    # ---------------------------------------------------------------- settle/meter

    async def _settle_and_meter(self, ctx, span: Span, started, preflight: Preflight,
                                quote, result: ProviderResult, rung_idx: int,
                                model_alias: str, deployment, guard, degraded: bool,
                                status: str, first_token_ms: int | None = None) -> None:
        # Cost detail: settle at the ACCURATE per-(provider, model_id) price for
        # the deployment that actually served the request, not just the ladder
        # rung alias used for the pre-flight reservation. The concrete model id
        # is deployment.deployment_name; falls back to the alias tier when no
        # exact price is published (see PriceTable.quote_for).
        actual_quote = quote
        if deployment is not None:
            actual_quote = self.prices.quote_for(
                deployment.provider, deployment.deployment_name, model_alias)
        actual_cents = actual_quote.cost_cents(result.input_tokens, result.output_tokens)
        state = await self.budgets.settle(preflight, actual_cents)
        span.set_attribute("windrose.budget_state", state)
        span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
        span.set_attribute("windrose.price_source", actual_quote.source)
        if self.anomaly is not None:
            await self.anomaly.observe(ctx.tenant_id, actual_cents)
        await self._record_and_meter(
            ctx, span, started, rung_idx, model_alias, deployment,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cost_usd=actual_cents / 100, cached=False, guardrail_flags=guard.flags,
            degraded=degraded, status=status, first_token_ms=first_token_ms,
            price_source=actual_quote.source,
        )

    async def _record_and_meter(self, ctx, span: Span, started, rung_idx: int,
                                model_alias: str, deployment, *, input_tokens: int,
                                output_tokens: int, cost_usd: float, cached: bool,
                                guardrail_flags: list[str], degraded: bool,
                                status: str, first_token_ms: int | None = None,
                                price_source: str | None = None) -> None:
        latency_ms = int((self.clock.now() - started).total_seconds() * 1000)
        day = self.clock.now().date().isoformat()
        if deployment is not None:
            self.usage_recorder.observe(deployment.id, day,
                                        input_tokens + output_tokens)
        payload = {
            "request_id": ctx.request_id,
            "tenant_id": ctx.tenant_id,
            "workspace_id": ctx.attribution.workspace_id,
            "principal": ctx.principal_sub,
            "agent_id": ctx.attribution.agent_id,
            "agent_version": ctx.attribution.agent_version,
            "tool": ctx.attribution.tool,
            "feature": ctx.attribution.feature,
            "request_class": ctx.request_class,
            "model_alias": model_alias,
            # Cost detail: provider + concrete provider-side model id so spend is
            # breakable-down by (provider, model, request_class); price_source
            # records whether the exact per-model price or the alias fallback
            # priced this request.
            "provider": deployment.provider if deployment else None,
            "model": deployment.deployment_name if deployment else None,
            "deployment": deployment.id if deployment else None,
            "rung": rung_idx,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached": cached,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "first_token_ms": first_token_ms,
            "guardrail_flags": list(guardrail_flags),
            "degraded": "budget" if degraded else None,
            "price_version": self.prices.version,
            "price_source": price_source,
            "trace_id": ctx.trace_id,
        }
        self.metrics.inc("aig_tokens_total", input_tokens + output_tokens,
                         tenant=ctx.tenant_id)
        self.metrics.inc("aig_spend_usd_total", cost_usd, tenant=ctx.tenant_id)
        self.metrics.inc("aig_rung_requests_total", tenant=ctx.tenant_id,
                         rung=str(rung_idx))
        async with self.uow_factory(ctx.tenant_id) as uow:
            await uow.request_log.add(RequestLog(
                request_id=ctx.request_id, tenant_id=ctx.tenant_id,
                principal=ctx.principal_sub, request_class=ctx.request_class,
                model_alias=model_alias, rung=rung_idx, input_tokens=input_tokens,
                output_tokens=output_tokens, cost_usd=cost_usd, cached=cached,
                guardrail_flags=list(guardrail_flags), status=status,
                latency_ms=latency_ms, trace_id=ctx.trace_id,
                deployment_id=deployment.id if deployment else None,
                created_at=self.clock.now(),
            ))
            await uow.outbox.add(self.settings.usage_topic, make_envelope(
                event_type="ai.token_usage.v1", tenant_id=ctx.tenant_id,
                actor=ctx.actor or {"type": "user", "id": ctx.principal_sub},
                via_agent=ctx.via_agent,
                resource_urn=f"wr:{ctx.tenant_id}:ai:request/{ctx.request_id}",
                trace_id=ctx.trace_id, payload=payload,
            ))
            await uow.commit()

    async def _log_request(self, ctx, started, *, rung: int, model_alias: str,
                           deployment_id: str | None, input_tokens: int,
                           output_tokens: int, cost_usd: float, cached: bool,
                           guardrail_flags: list[str], status: str) -> None:
        latency_ms = int((self.clock.now() - started).total_seconds() * 1000)
        try:
            async with self.uow_factory(ctx.tenant_id) as uow:
                await uow.request_log.add(RequestLog(
                    request_id=ctx.request_id, tenant_id=ctx.tenant_id,
                    principal=ctx.principal_sub, request_class=ctx.request_class,
                    model_alias=model_alias, rung=rung, input_tokens=input_tokens,
                    output_tokens=output_tokens, cost_usd=cost_usd, cached=cached,
                    guardrail_flags=list(guardrail_flags), status=status,
                    latency_ms=latency_ms, trace_id=ctx.trace_id,
                    deployment_id=deployment_id, created_at=self.clock.now(),
                ))
                await uow.commit()
        except Exception:  # noqa: BLE001 - request logging must not mask the rejection
            pass

    async def _emit_guardrail_events(self, ctx: RequestCtx, events: list[dict]) -> None:
        async with self.uow_factory(ctx.tenant_id) as uow:
            for payload in events:
                await uow.outbox.add(self.settings.events_topic, make_envelope(
                    event_type="guardrail.triggered", tenant_id=ctx.tenant_id,
                    actor=ctx.actor or {"type": "user", "id": ctx.principal_sub},
                    via_agent=ctx.via_agent,
                    resource_urn=f"wr:{ctx.tenant_id}:ai:request/{ctx.request_id}",
                    trace_id=ctx.trace_id, payload=payload,
                ))
            await uow.commit()
