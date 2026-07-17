"""Application services orchestrating the eval flywheel (BRD §3).

Storage-agnostic: each service takes a ``ServiceDeps`` bundle exposing a
``uow_factory(tenant_id)`` (memory or SQL), a clock, settings, the scorer
registry, the eval runner and an outbox-backed event emit. Every write that must
be observed externally is emitted through the transactional outbox (MASTER-FR-034).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain import gate_rule
from app.domain import slo as slo_mod
from app.domain.canary import compare as canary_compare
from app.domain.entities import (
    CallCtx,
    CanaryComparison,
    Dataset,
    EvalCase,
    EvalRun,
    GateResult,
    Scorer,
    Suite,
)
from app.domain.errors import (
    AnonymizationRequired,
    BaselineIncomparable,
    Conflict,
    EvalBudgetExceeded,
    FrozenDataset,
    JudgeAgreementTooLow,
    JudgeGatesAlone,
    NotFound,
    ValidationFailed,
)
from app.domain.runner import EvalRunner
from app.domain.scorers.registry import ScorerRegistry
from app.events.envelope import make_envelope
from app.utils import new_id


@dataclass
class ServiceDeps:
    settings: object
    clock: object
    uow_factory: object
    registry: ScorerRegistry
    runner_factory: object  # callable(candidate_provider) -> EvalRunner
    events_topic: str = "eval.events.v1"
    extras: dict = field(default_factory=dict)

    def now(self) -> datetime:
        return self.clock.now()


def _urn(tenant, kind, ident) -> str:
    return f"wr:{tenant}:eval:{kind}/{ident}"


# --------------------------------------------------------------- datasets


class DatasetService:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def create(self, ctx: CallCtx, body: dict) -> Dataset:
        now = self.deps.now()
        dataset_key = body["dataset_key"]
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            latest = await uow.datasets.latest(dataset_key)
            version = (
                1
                if latest is None
                else (latest.version if latest.status == "draft" else latest.version + 1)
            )
            if latest and latest.status == "draft":
                return latest
            d = Dataset(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                dataset_key=dataset_key,
                agent_key=body["agent_key"],
                version=version,
                status="draft",
                description=body.get("description"),
                case_count=0,
                provenance_summary=body.get("provenance_summary", {}),
                frozen_by=None,
                frozen_at=None,
                created_by=ctx.actor.get("id", "unknown"),
                created_at=now,
                updated_at=now,
            )
            await uow.datasets.create(d)
            await uow.commit()
            return d

    async def get(self, ctx: CallCtx, dataset_key: str, version: int | None = None) -> Dataset:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            d = await (
                uow.datasets.get(dataset_key, version)
                if version is not None
                else uow.datasets.latest(dataset_key)
            )
        if d is None:
            raise NotFound(f"dataset {dataset_key} v{version} not found")
        return d

    async def list(self, ctx: CallCtx, agent_key=None, limit=50, cursor=None):
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            return await uow.datasets.list(agent_key, limit, cursor)

    async def ensure_draft(self, ctx: CallCtx, dataset_key: str) -> Dataset:
        """Copy-on-write: if the latest version is frozen, create the next draft
        version by copying its active cases (AC-15)."""
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            latest = await uow.datasets.latest(dataset_key)
            if latest is None:
                raise NotFound(f"dataset {dataset_key} not found")
            if latest.status == "draft":
                return latest
            new_ver = latest.version + 1
            d = Dataset(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                dataset_key=dataset_key,
                agent_key=latest.agent_key,
                version=new_ver,
                status="draft",
                description=latest.description,
                case_count=0,
                provenance_summary=dict(latest.provenance_summary),
                frozen_by=None,
                frozen_at=None,
                created_by=ctx.actor.get("id", "unknown"),
                created_at=now,
                updated_at=now,
            )
            await uow.datasets.create(d)
            active = await uow.cases.active_for(dataset_key, latest.version)
            for c in active:
                await uow.cases.add(
                    EvalCase(
                        id=new_id(),
                        tenant_id=ctx.tenant_id,
                        dataset_key=dataset_key,
                        dataset_version=new_ver,
                        input=c.input,
                        expected=c.expected,
                        source=c.source,
                        source_ref=c.source_ref,
                        source_tenant_id=c.source_tenant_id,
                        tags=list(c.tags),
                        weight=c.weight,
                        status="active",
                        anonymization_attested_by=c.anonymization_attested_by,
                        created_at=now,
                        updated_at=now,
                    )
                )
            d.case_count = len(active)
            await uow.datasets.update(d)
            await uow.commit()
            return d

    async def freeze(self, ctx: CallCtx, dataset_key: str, version: int) -> Dataset:
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            d = await uow.datasets.get(dataset_key, version)
            if d is None:
                raise NotFound(f"dataset {dataset_key} v{version} not found")
            if d.status == "frozen":
                raise Conflict("dataset version already frozen")
            active = await uow.cases.count_active(dataset_key, version)
            if active < 1:
                raise ValidationFailed("freeze requires ≥1 active case (freeze guard)")
            d.status = "frozen"
            d.frozen_by = ctx.actor.get("id", "unknown")
            d.frozen_at = now
            d.case_count = active
            d.updated_at = now
            await uow.datasets.update(d)
            await uow.outbox.add(
                self.deps.events_topic,
                make_envelope(
                    event_type="dataset.version_frozen",
                    tenant_id=ctx.tenant_id,
                    actor=ctx.actor,
                    via_agent=ctx.via_agent,
                    resource_urn=_urn(ctx.tenant_id, "dataset", f"{dataset_key}@{version}"),
                    payload={"dataset_key": dataset_key, "version": version, "case_count": active},
                    trace_id=ctx.trace_id,
                ),
            )
            await uow.commit()
            return d


# --------------------------------------------------------------- cases


class CaseService:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def _target_draft_version(self, uow, ctx, dataset_key, agent_key) -> int:
        latest = await uow.datasets.latest(dataset_key)
        now = self.deps.now()
        if latest is None:
            d = Dataset(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                dataset_key=dataset_key,
                agent_key=agent_key,
                version=1,
                status="draft",
                description=None,
                case_count=0,
                provenance_summary={},
                frozen_by=None,
                frozen_at=None,
                created_by=ctx.actor.get("id", "system"),
                created_at=now,
                updated_at=now,
            )
            await uow.datasets.create(d)
            return 1
        if latest.status == "draft":
            return latest.version
        # frozen -> open next draft (copy-on-write of active cases)
        new_ver = latest.version + 1
        d = Dataset(
            id=new_id(),
            tenant_id=ctx.tenant_id,
            dataset_key=dataset_key,
            agent_key=latest.agent_key,
            version=new_ver,
            status="draft",
            description=latest.description,
            case_count=0,
            provenance_summary=dict(latest.provenance_summary),
            frozen_by=None,
            frozen_at=None,
            created_by=ctx.actor.get("id", "system"),
            created_at=now,
            updated_at=now,
        )
        await uow.datasets.create(d)
        for c in await uow.cases.active_for(dataset_key, latest.version):
            await uow.cases.add(
                EvalCase(
                    id=new_id(),
                    tenant_id=ctx.tenant_id,
                    dataset_key=dataset_key,
                    dataset_version=new_ver,
                    input=c.input,
                    expected=c.expected,
                    source=c.source,
                    source_ref=c.source_ref,
                    source_tenant_id=c.source_tenant_id,
                    tags=list(c.tags),
                    weight=c.weight,
                    status="active",
                    anonymization_attested_by=c.anonymization_attested_by,
                    created_at=now,
                    updated_at=now,
                )
            )
        return new_ver

    async def create(self, ctx: CallCtx, body: dict) -> EvalCase:
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            version = await self._target_draft_version(
                uow, ctx, body["dataset_key"], body.get("agent_key", "unknown")
            )
            status = body.get("status", "candidate")
            c = EvalCase(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                dataset_key=body["dataset_key"],
                dataset_version=version,
                input=body["input"],
                expected=body["expected"],
                source=body.get("source", "manual"),
                source_ref=body.get("source_ref"),
                source_tenant_id=body.get("source_tenant_id"),
                tags=body.get("tags", []),
                weight=body.get("weight", 1.0),
                status=status,
                anonymization_attested_by=body.get("anonymization_attested_by"),
                created_at=now,
                updated_at=now,
            )
            await uow.cases.add(c)
            await self._bump_count(uow, ctx, body["dataset_key"], version)
            await uow.commit()
            return c

    async def _bump_count(self, uow, ctx, dataset_key, version):
        d = await uow.datasets.get(dataset_key, version)
        if d:
            d.case_count = await uow.cases.count_active(dataset_key, version)
            d.updated_at = self.deps.now()
            await uow.datasets.update(d)

    # ---- flywheel sourcing (EVL-FR-003) ----

    async def from_verified_query(self, ctx: CallCtx, payload: dict) -> EvalCase | None:
        """(a) verified query -> auto-ACTIVE nl2sql case (AC-4)."""
        dataset_key = (
            payload.get("dataset_key") or f"{payload.get('agent_key', 'analytics')}/nl2sql"
        )
        source_ref = payload["verified_query_urn"]
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            existing = await uow.cases.find_by_source_ref(source_ref)
            version = await self._target_draft_version(
                uow, ctx, dataset_key, payload.get("agent_key", "analytics")
            )
            case_body = EvalCase(
                id=existing.id if existing else new_id(),
                tenant_id=ctx.tenant_id,
                dataset_key=dataset_key,
                dataset_version=version,
                input={
                    "messages": [{"role": "user", "content": payload["nl"]}],
                    "context_refs": payload.get("context_refs", {}),
                },
                expected={
                    "kind": "sql_result",
                    "value": {
                        "sql": payload["sql"],
                        "float_tolerance": payload.get("float_tolerance", 0.01),
                        "order_insensitive": payload.get("order_insensitive", True),
                    },
                },
                source="verified_query",
                source_ref=source_ref,
                source_tenant_id=None,
                tags=payload.get("tags", ["verified_query"]),
                weight=1.0,
                status="active",
                anonymization_attested_by=None,
                created_at=now,
                updated_at=now,
            )
            if existing:
                await uow.cases.update(case_body)
            else:
                await uow.cases.add(case_body)
            await self._bump_count(uow, ctx, dataset_key, version)
            await uow.outbox.add(
                self.deps.events_topic,
                make_envelope(
                    event_type="case.promoted",
                    tenant_id=ctx.tenant_id,
                    actor=ctx.actor,
                    resource_urn=_urn(ctx.tenant_id, "case", case_body.id),
                    payload={
                        "source": "verified_query",
                        "dataset_key": dataset_key,
                        "status": "active",
                    },
                    trace_id=ctx.trace_id,
                ),
            )
            await uow.commit()
            return case_body

    async def from_rejection(self, ctx: CallCtx, payload: dict) -> EvalCase:
        """(c) HITL rejection -> CANDIDATE case; expected = NOT the proposed action,
        rejection reason attached (AC-5)."""
        dataset_key = payload.get("dataset_key") or f"{payload['agent_key']}/proposals"
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            version = await self._target_draft_version(uow, ctx, dataset_key, payload["agent_key"])
            c = EvalCase(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                dataset_key=dataset_key,
                dataset_version=version,
                input=payload.get("run_context", {}),
                expected={
                    "kind": "rubric",
                    "value": {
                        "not_action": payload.get("proposed_action"),
                        "rejection_reason": payload.get("reason"),
                    },
                },
                source="hitl_rejection",
                source_ref=payload.get("proposal_urn"),
                source_tenant_id=ctx.tenant_id,
                tags=payload.get("tags", ["hitl_rejection"]),
                weight=1.0,
                status="candidate",
                anonymization_attested_by=None,
                created_at=now,
                updated_at=now,
            )
            await uow.cases.add(c)
            await uow.commit()
            return c

    async def from_edit_diff(self, ctx: CallCtx, payload: dict) -> EvalCase:
        """(d) approval edit-diff -> CANDIDATE case; expected = edited args; diff is
        the supervision label (AC-6)."""
        dataset_key = payload.get("dataset_key") or f"{payload['agent_key']}/proposals"
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            version = await self._target_draft_version(uow, ctx, dataset_key, payload["agent_key"])
            c = EvalCase(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                dataset_key=dataset_key,
                dataset_version=version,
                input=payload.get("run_context", {}),
                expected={
                    "kind": "proposal",
                    "value": {
                        "tool": payload.get("tool"),
                        "args": payload.get("edited_args", {}),
                        "diff": payload.get("diff", {}),
                    },
                },
                source="approval_edit_diff",
                source_ref=payload.get("proposal_urn"),
                source_tenant_id=ctx.tenant_id,
                tags=payload.get("tags", ["approval_edit_diff"]),
                weight=1.0,
                status="candidate",
                anonymization_attested_by=None,
                created_at=now,
                updated_at=now,
            )
            await uow.cases.add(c)
            await uow.commit()
            return c

    async def list_queue(
        self,
        ctx: CallCtx,
        dataset_key=None,
        dataset_version=None,
        status="candidate",
        source=None,
        tags=None,
        limit=50,
        cursor=None,
    ):
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            return await uow.cases.list(
                dataset_key, dataset_version, status, source, tags, limit, cursor
            )

    async def get(self, ctx: CallCtx, case_id: str) -> EvalCase:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = await uow.cases.get(case_id)
        if c is None:
            raise NotFound("case not found")
        return c

    async def promote(self, ctx: CallCtx, case_id: str) -> EvalCase:
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = await uow.cases.get(case_id)
            if c is None:
                raise NotFound("case not found")
            await self._assert_mutable(uow, c)
            # BR-3: production-sourced cases require anonymization attestation.
            if (
                c.source in ("production_trace", "hitl_rejection", "approval_edit_diff")
                and not c.anonymization_attested_by
            ):
                raise AnonymizationRequired(
                    "promotion of a production-sourced case requires anonymization attestation"
                )
            c.status = "active"
            c.updated_at = now
            await uow.cases.update(c)
            await self._bump_count(uow, ctx, c.dataset_key, c.dataset_version)
            await uow.outbox.add(
                self.deps.events_topic,
                make_envelope(
                    event_type="case.promoted",
                    tenant_id=ctx.tenant_id,
                    actor=ctx.actor,
                    resource_urn=_urn(ctx.tenant_id, "case", c.id),
                    payload={"source": c.source, "dataset_key": c.dataset_key, "status": "active"},
                    trace_id=ctx.trace_id,
                ),
            )
            await uow.commit()
            return c

    async def attest(self, ctx: CallCtx, case_id: str, attested_by: str) -> EvalCase:
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = await uow.cases.get(case_id)
            if c is None:
                raise NotFound("case not found")
            c.anonymization_attested_by = attested_by
            c.updated_at = now
            await uow.cases.update(c)
            await uow.commit()
            return c

    async def reject(self, ctx: CallCtx, case_id: str) -> EvalCase:
        return await self._set_status(ctx, case_id, "retired")

    async def retire(self, ctx: CallCtx, case_id: str) -> EvalCase:
        return await self._set_status(ctx, case_id, "retired")

    async def edit(self, ctx: CallCtx, case_id: str, patch: dict) -> EvalCase:
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = await uow.cases.get(case_id)
            if c is None:
                raise NotFound("case not found")
            await self._assert_mutable(uow, c)
            for k in ("input", "expected", "tags", "weight", "anonymization_attested_by"):
                if k in patch:
                    setattr(c, k, patch[k])
            c.updated_at = now
            await uow.cases.update(c)
            await uow.commit()
            return c

    async def _set_status(self, ctx, case_id, status) -> EvalCase:
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = await uow.cases.get(case_id)
            if c is None:
                raise NotFound("case not found")
            await self._assert_mutable(uow, c)
            c.status = status
            c.updated_at = now
            await uow.cases.update(c)
            await self._bump_count(uow, ctx, c.dataset_key, c.dataset_version)
            await uow.commit()
            return c

    async def _assert_mutable(self, uow, c: EvalCase) -> None:
        d = await uow.datasets.get(c.dataset_key, c.dataset_version)
        if d and d.status == "frozen":
            raise FrozenDataset(
                f"dataset {c.dataset_key} v{c.dataset_version} is frozen; "
                "copy-on-write to the next draft version is required (AC-15)"
            )


# --------------------------------------------------------------- scorers


class ScorerService:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def seed_builtins(self, ctx: CallCtx) -> None:
        from app.domain.scorers.registry import SCORER_META

        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            for key, meta in SCORER_META.items():
                if await uow.scorers.get(key, meta["version"]) is None:
                    await uow.scorers.upsert(
                        Scorer(
                            id=new_id(),
                            tenant_id=ctx.tenant_id,
                            scorer_key=key,
                            version=meta["version"],
                            kind=meta["kind"],
                            gate_eligible=meta["gate_eligible"],
                            config_schema={},
                            applicable_expected_kinds=[],
                            image_ref=None,
                            judge_prompt_ref=None,
                            judge_prompt_ver=None,
                            judge_agreement=None,
                            status="active",
                            created_at=now,
                        )
                    )
            await uow.commit()

    async def register(self, ctx: CallCtx, body: dict) -> Scorer:
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            sc = Scorer(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                scorer_key=body["scorer_key"],
                version=body["version"],
                kind=body["kind"],
                gate_eligible=body["kind"] == "deterministic" and body.get("gate_eligible", True),
                config_schema=body.get("config_schema", {}),
                applicable_expected_kinds=body.get("applicable_expected_kinds", []),
                image_ref=body.get("image_ref"),
                judge_prompt_ref=body.get("judge_prompt_ref"),
                judge_prompt_ver=body.get("judge_prompt_ver"),
                judge_agreement=body.get("judge_agreement"),
                status=body.get("status", "draft"),
                created_at=now,
            )
            await uow.scorers.upsert(sc)
            await uow.commit()
            return sc

    async def activate(self, ctx: CallCtx, scorer_key: str, version: int) -> Scorer:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            sc = await uow.scorers.get(scorer_key, version)
            if sc is None:
                raise NotFound("scorer version not found")
            # EVL-FR-014 / AC-11: judge activation blocked when agreement < 0.8.
            if (
                sc.kind == "llm_judge"
                and sc.judge_agreement is not None
                and sc.judge_agreement < 0.8
            ):
                raise JudgeAgreementTooLow(
                    f"judge-vs-human agreement {sc.judge_agreement} < 0.8 blocks activation"
                )
            sc.status = "active"
            await uow.scorers.upsert(sc)
            await uow.commit()
            return sc

    async def update(
        self, ctx: CallCtx, scorer_key: str, patch: dict, version: int | None = None
    ) -> Scorer:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            sc = (
                await uow.scorers.get(scorer_key, version)
                if version is not None
                else await uow.scorers.latest(scorer_key)
            )
            if sc is None:
                raise NotFound("scorer not found")
            # scorer_key/version/kind are the identity and are immutable.
            for k in (
                "gate_eligible",
                "config_schema",
                "applicable_expected_kinds",
                "image_ref",
                "judge_prompt_ref",
                "judge_prompt_ver",
                "judge_agreement",
                "status",
            ):
                if k in patch:
                    setattr(sc, k, patch[k])
            # BR-1: only deterministic scorers may gate (mirrors register()).
            if sc.kind != "deterministic":
                sc.gate_eligible = False
            await uow.scorers.upsert(sc)
            await uow.commit()
            return sc

    async def list(self, ctx: CallCtx, limit=200, cursor=None):
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            return await uow.scorers.list(limit, cursor)


# --------------------------------------------------------------- suites


class SuiteService:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def create(self, ctx: CallCtx, body: dict) -> Suite:
        # BR-1: gate rule must reference ≥1 deterministic scorer (validated at save).
        try:
            gate_rule.validate(body["gate_rule"])
        except gate_rule.GateRuleError as exc:
            if "deterministic" in str(exc):
                raise JudgeGatesAlone(str(exc)) from exc
            raise ValidationFailed(f"invalid gate rule: {exc}") from exc
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            version = await uow.suites.next_version(body["suite_id"])
            su = Suite(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                suite_id=body["suite_id"],
                agent_key=body["agent_key"],
                version=version,
                datasets=body["datasets"],
                scorers=body["scorers"],
                gate_rule=body["gate_rule"],
                baseline_version=body.get("baseline_version"),
                judge_ladder_pin=body.get("judge_ladder_pin", {}),
                min_cases=body.get("min_cases", 0),
                created_at=now,
            )
            await uow.suites.add(su)
            await uow.commit()
            return su

    async def get(self, ctx: CallCtx, suite_id: str, version: int | None = None) -> Suite:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            su = await uow.suites.get(suite_id, version)
        if su is None:
            raise NotFound("suite not found")
        return su

    async def update(
        self, ctx: CallCtx, suite_id: str, patch: dict, version: int | None = None
    ) -> Suite:
        # BR-1: a changed gate rule must still reference ≥1 deterministic scorer.
        if "gate_rule" in patch:
            try:
                gate_rule.validate(patch["gate_rule"])
            except gate_rule.GateRuleError as exc:
                if "deterministic" in str(exc):
                    raise JudgeGatesAlone(str(exc)) from exc
                raise ValidationFailed(f"invalid gate rule: {exc}") from exc
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            su = await uow.suites.get(suite_id, version)
            if su is None:
                raise NotFound("suite not found")
            # suite_id/agent_key/version are the identity and are immutable.
            for k in (
                "datasets",
                "scorers",
                "gate_rule",
                "baseline_version",
                "judge_ladder_pin",
                "min_cases",
            ):
                if k in patch:
                    setattr(su, k, patch[k])
            await uow.suites.update(su)
            await uow.commit()
            return su


# --------------------------------------------------------------- runs


class RunService:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def create_and_execute(
        self,
        ctx: CallCtx,
        *,
        trigger: str,
        agent_key: str,
        candidate: dict,
        suite_id: str,
        suite_version: int | None = None,
        candidate_provider,
        baseline: dict | None = None,
        memory_snapshot_ver: str | None = None,
        cost_cap_usd: float | None = None,
    ) -> EvalRun:
        now = self.deps.now()
        cap = cost_cap_usd or self.deps.settings.default_run_cost_cap_usd
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            suite = await uow.suites.get(suite_id, suite_version)
            if suite is None:
                raise NotFound(f"suite {suite_id} not found")
            dataset_pin = suite.datasets[0]
            cases = [
                self._case_dict(c)
                for c in await uow.cases.active_for(
                    dataset_pin["dataset_key"], dataset_pin["version"]
                )
            ]
            run = EvalRun(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                trigger=trigger,
                agent_key=agent_key,
                candidate=candidate,
                baseline=baseline,
                suite_pins={
                    "suite_id": suite.suite_id,
                    "suite_version": suite.version,
                    "datasets": suite.datasets,
                    "scorers": suite.scorers,
                    "gate_rule": suite.gate_rule,
                    "judge_ladder_pin": suite.judge_ladder_pin,
                    "baseline_version": suite.baseline_version,
                },
                memory_snapshot_ver=memory_snapshot_ver,
                status="running",
                totals={},
                cost_usd=0.0,
                cost_cap_usd=cap,
                temporal_workflow_id=None,
                started_by=ctx.actor.get("id", "unknown"),
                created_at=now,
                updated_at=now,
            )
            await uow.runs.add(run)
            await uow.commit()

        runner: EvalRunner = self.deps.runner_factory(candidate_provider)
        outcome = await runner.run(
            run_id=run.id,
            tenant_id=ctx.tenant_id,
            agent_key=agent_key,
            candidate=candidate,
            suite_scorers=suite.scorers,
            cases=cases,
            cost_cap_usd=cap,
            memory_snapshot_ver=memory_snapshot_ver,
        )

        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            await uow.case_results.add_many(outcome.case_results)
            run = await uow.runs.get(run.id)
            run.totals = outcome.totals
            run.cost_usd = outcome.cost_usd
            run.status = outcome.status
            run.updated_at = self.deps.now()
            await uow.runs.update(run)
            event_type = (
                "eval_run.completed" if outcome.status == "completed" else "eval_run.failed"
            )
            await uow.outbox.add(
                self.deps.events_topic,
                make_envelope(
                    event_type=event_type,
                    tenant_id=ctx.tenant_id,
                    actor=ctx.actor,
                    resource_urn=_urn(ctx.tenant_id, "run", run.id),
                    payload={
                        "run_id": run.id,
                        "agent_key": agent_key,
                        "status": outcome.status,
                        "totals": outcome.totals,
                        "error": outcome.error,
                    },
                    trace_id=ctx.trace_id,
                ),
            )
            await uow.commit()
        if outcome.error == "EVAL_BUDGET_EXCEEDED":
            raise EvalBudgetExceeded(
                f"eval run exceeded cost cap ${cap} (spent ${outcome.cost_usd:.4f}); "
                f"partial results retained (run {run.id})"
            )
        return run

    async def get(self, ctx: CallCtx, run_id: str) -> EvalRun:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            r = await uow.runs.get(run_id)
        if r is None:
            raise NotFound("run not found")
        return r

    async def list_cases(self, ctx: CallCtx, run_id: str):
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            if await uow.runs.get(run_id) is None:
                raise NotFound("run not found")
            return await uow.case_results.list_by_run(run_id)

    async def cancel(self, ctx: CallCtx, run_id: str) -> EvalRun:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            r = await uow.runs.get(run_id)
            if r is None:
                raise NotFound("run not found")
            if r.status in ("completed", "failed"):
                raise Conflict("run already terminal")
            r.status = "failed"
            r.totals = {**r.totals, "cancelled": True}
            r.updated_at = self.deps.now()
            await uow.runs.update(r)
            await uow.commit()
            return r

    async def list(self, ctx: CallCtx, agent_key=None, trigger=None, limit=50, cursor=None):
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            return await uow.runs.list(agent_key, trigger, limit, cursor)

    @staticmethod
    def _case_dict(c: EvalCase) -> dict:
        return {
            "id": c.id,
            "dataset_key": c.dataset_key,
            "dataset_version": c.dataset_version,
            "input": c.input,
            "expected": c.expected,
            "tags": c.tags,
            "weight": c.weight,
        }


# --------------------------------------------------------------- gates


class GateService:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def evaluate_from_run(self, ctx: CallCtx, run_id: str) -> GateResult:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise NotFound("run not found")
            content_digest_v = run.candidate.get("content_digest", "")
            suite_pins = run.suite_pins
            gate_rule_expr = suite_pins["gate_rule"]
            dataset_pin = suite_pins["datasets"][0]
            dataset_version = dataset_pin["version"]

            # BR-1 defense-in-depth: re-assert judge-never-gates-alone at gate time.
            try:
                gate_rule.validate(gate_rule_expr)
            except gate_rule.GateRuleError as exc:
                raise JudgeGatesAlone(str(exc)) from exc

            candidate_agg = run.totals.get("aggregates", {})

            # Baseline resolution (EVL-FR-030 / BR-2): the baseline gate/run for the
            # same dataset version. Mismatch -> BASELINE_INCOMPARABLE (fail safe).
            baseline_agg = None
            if run.baseline:
                baseline_agg = run.baseline.get("aggregates")
                b_dsv = run.baseline.get("dataset_version")
                if b_dsv is not None and b_dsv != dataset_version:
                    raise BaselineIncomparable(
                        f"baseline dataset version {b_dsv} != candidate {dataset_version}"
                    )

            needs_baseline = any(t.uses_baseline for t in gate_rule.parse(gate_rule_expr)[0])
            if needs_baseline and baseline_agg is None:
                raise BaselineIncomparable(
                    "gate rule is baseline-relative but no comparable baseline scores exist "
                    "(a baseline re-run on this dataset version is required)"
                )

            gate_passed, verdicts = gate_rule.evaluate(gate_rule_expr, candidate_agg, baseline_agg)

            results = await uow.case_results.list_by_run(run_id)
            failed_sample = [
                {
                    "case_id": r.case_id,
                    "scorer": f"{r.scorer_key}@{r.scorer_version}",
                    "details": r.details,
                    "trace_ref": r.trace_ref,
                }
                for r in results
                if not r.passed
            ][:10]

            verdicts_out = [
                {
                    "scorer": f"{v.scorer}@{_ver(suite_pins, v.scorer)}",
                    "aggregate": v.value,
                    "baseline": v.baseline,
                    "threshold": v.threshold,
                    "passed": v.passed,
                }
                for v in verdicts
            ]
            gate_run_id = f"gr-{new_id()[:12]}"
            existing = await uow.gates.find(
                run.agent_key,
                content_digest_v,
                suite_pins["suite_id"],
                suite_pins["suite_version"],
                dataset_version,
            )
            if existing:
                return existing
            gate = GateResult(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                gate_run_id=gate_run_id,
                run_id=run_id,
                agent_key=run.agent_key,
                content_digest=content_digest_v,
                suite_id=suite_pins["suite_id"],
                suite_version=suite_pins["suite_version"],
                dataset_version=dataset_version,
                gate_passed=gate_passed,
                verdicts=verdicts_out,
                failed_cases_sample=failed_sample,
                report_url=f"/api/v1/runs/{run_id}",
                created_at=self.deps.now(),
            )
            await uow.gates.add(gate)
            await uow.outbox.add(
                self.deps.events_topic,
                make_envelope(
                    event_type="gate.completed",
                    tenant_id=ctx.tenant_id,
                    actor=ctx.actor,
                    resource_urn=_urn(ctx.tenant_id, "gate", gate_run_id),
                    payload={
                        "gate_run_id": gate_run_id,
                        "agent_key": run.agent_key,
                        "content_digest": content_digest_v,
                        "gate_passed": gate_passed,
                        "suite_id": suite_pins["suite_id"],
                        "suite_version": suite_pins["suite_version"],
                        "dataset_version": dataset_version,
                    },
                    trace_id=ctx.trace_id,
                ),
            )
            await uow.commit()
            return gate

    async def get(self, ctx: CallCtx, gate_run_id: str) -> GateResult:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            g = await uow.gates.get(gate_run_id)
        if g is None:
            raise NotFound("gate result not found")
        return g

    async def find_by_digest(self, ctx: CallCtx, agent_key: str, content_digest: str):
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            return await uow.gates.find_by_digest(agent_key, content_digest)


def _ver(suite_pins: dict, scorer_key: str) -> int:
    for s in suite_pins.get("scorers", []):
        if s["scorer"] == scorer_key:
            return s.get("version", 1)
    return 1


# --------------------------------------------------------------- canary


class CanaryService:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def create(self, ctx: CallCtx, body: dict) -> CanaryComparison:
        now = self.deps.now()
        cid = f"cc-{new_id()[:12]}"
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = CanaryComparison(
                id=new_id(),
                tenant_id=ctx.tenant_id,
                comparison_id=cid,
                agent_key=body["agent_key"],
                candidate_version=body["candidate_version"],
                baseline_version=body["baseline_version"],
                sample_spec=body.get("sample_spec", {"min_samples": 200}),
                mode=body.get("mode", "paired_shadow"),
                status="collecting",
                report={
                    "thresholds": body.get("thresholds", {}),
                    "must_scorers": body.get("must_scorers", []),
                },
                samples=0,
                created_at=now,
                updated_at=now,
            )
            await uow.canaries.add(c)
            await uow.commit()
            return c

    async def ingest_samples(
        self, ctx: CallCtx, comparison_id: str, paired_scores: dict
    ) -> CanaryComparison:
        """paired_scores: {scorer: [[candidate, baseline], ...]}. Recomputes the
        report; sets ready/failed_early per EVL-FR-041/042."""
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = await uow.canaries.get(comparison_id)
            if c is None:
                raise NotFound("canary comparison not found")
            thresholds = c.report.get("thresholds", {})
            must = set(c.report.get("must_scorers", []))
            pairs = {k: [tuple(p) for p in v] for k, v in paired_scores.items()}
            result = canary_compare(pairs, thresholds, must)
            c.samples = result["samples"]
            min_samples = c.sample_spec.get("min_samples", 200)
            report = {**c.report, **result}
            if result["early_stop"]:
                c.status = "failed_early"
                event = "canary.failed_early"
                payload = {
                    "comparison_id": comparison_id,
                    "regressing_scorers": [result["early_stop"]["scorer"]],
                }
            elif result["samples"] >= min_samples:
                c.status = "ready"
                event = "canary.scored"
                payload = {
                    "comparison_id": comparison_id,
                    "status": "ready",
                    "summary": {
                        "recommendation": result["recommendation"],
                        "metrics": result["metrics"],
                    },
                }
            else:
                event = None
                payload = None
            c.report = report
            c.updated_at = self.deps.now()
            await uow.canaries.update(c)
            if event:
                await uow.outbox.add(
                    self.deps.events_topic,
                    make_envelope(
                        event_type=event,
                        tenant_id=ctx.tenant_id,
                        actor=ctx.actor,
                        resource_urn=_urn(ctx.tenant_id, "canary", comparison_id),
                        payload=payload,
                        trace_id=ctx.trace_id,
                    ),
                )
            await uow.commit()
            return c

    async def get(self, ctx: CallCtx, comparison_id: str) -> CanaryComparison:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = await uow.canaries.get(comparison_id)
        if c is None:
            raise NotFound("canary comparison not found")
        return c

    async def stop(self, ctx: CallCtx, comparison_id: str) -> CanaryComparison:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            c = await uow.canaries.get(comparison_id)
            if c is None:
                raise NotFound("canary comparison not found")
            if c.status == "collecting":
                c.status = "expired"
            c.updated_at = self.deps.now()
            await uow.canaries.update(c)
            await uow.commit()
            return c


# --------------------------------------------------------------- trends


class TrendService:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def trends(
        self, ctx: CallCtx, agent_key: str, scorer: str | None = None, window: str = "30d"
    ):
        """Score trend per version/time bucket from completed runs (EVL-FR-050)."""
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            page = await uow.runs.list(agent_key=agent_key, limit=200)
            points = []
            for run in page.items:
                if run.status != "completed":
                    continue
                aggs = run.totals.get("aggregates", {})
                for scorer_key, agg in aggs.items():
                    if scorer and scorer_key != scorer:
                        continue
                    points.append(
                        {
                            "run_id": run.id,
                            "agent_version": run.candidate.get("agent_version")
                            or run.candidate.get("content_digest"),
                            "scorer": scorer_key,
                            "mean": agg.get("mean"),
                            "pass_rate": agg.get("pass_rate"),
                            "at": run.created_at.isoformat(),
                        }
                    )
            points.sort(key=lambda p: p["at"])
            return points


# --------------------------------------------------------------- SLO


def window_start(window: str, now: datetime) -> datetime:
    now = now.astimezone(UTC)
    if window == "1h":
        return now.replace(minute=0, second=0, microsecond=0)
    if window == "24h":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "7d":
        monday = now - timedelta(days=now.weekday())
        return monday.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "30d":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(minute=0, second=0, microsecond=0)


class SloService:
    """Streaming SLO rollups (EVL-FR-051). Folds events into per-window counters
    for both a per-tenant row and a platform (tenant_id NULL) rollup, so operators
    see the cross-tenant view and tenant admins see only their slice (AC-10/AC-14)."""

    def __init__(self, deps: ServiceDeps):
        self.deps = deps

    async def ingest_event(
        self, tenant_id: str, agent_key: str, agent_version: str | None, kind: str, payload: dict
    ) -> list[dict]:
        now = self.deps.now()
        alerts_out: list[dict] = []
        async with self.deps.uow_factory(tenant_id) as uow:
            for scope_tenant in (tenant_id, None):
                for window in self.deps.settings.slo_windows:
                    ws = window_start(window, now)
                    row = await uow.slo.get_or_create(
                        agent_key, agent_version, scope_tenant, window, ws
                    )
                    counters = dict(row.counters)
                    _fold(counters, kind, payload)
                    row.counters = counters
                    row.sample_n = counters.get("total_runs", 0)
                    row.updated_at = now
                    await uow.slo.save(row)
                    metrics = slo_mod.compute_metrics(counters)
                    alerts = slo_mod.budget_burn(metrics, row.targets)
                    for a in alerts:
                        alerts_out.append({**a, "agent_key": agent_key, "window": window})
                        await uow.outbox.add(
                            self.deps.events_topic,
                            make_envelope(
                                event_type="slo.budget_burn",
                                tenant_id=tenant_id,
                                actor={"type": "service", "id": "eval-service"},
                                resource_urn=_urn(tenant_id, "slo", agent_key),
                                payload={
                                    "agent_key": agent_key,
                                    "metric": a["metric"],
                                    "burn_rate": a["burn_rate"],
                                    "window": window,
                                },
                            ),
                        )
            await uow.commit()
        return alerts_out

    async def set_targets(
        self, ctx: CallCtx, agent_key: str, agent_version: str | None, targets: dict
    ) -> None:
        now = self.deps.now()
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            for scope_tenant in (ctx.tenant_id, None):
                for window in self.deps.settings.slo_windows:
                    ws = window_start(window, now)
                    row = await uow.slo.get_or_create(
                        agent_key, agent_version, scope_tenant, window, ws
                    )
                    row.targets = targets
                    row.updated_at = now
                    await uow.slo.save(row)
            await uow.commit()

    async def query(
        self, ctx: CallCtx, agent_key: str, window: str = "24h", operator: bool = False
    ):
        tenant_filter = None if operator else ctx.tenant_id
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            rows = await uow.slo.list(agent_key, tenant_filter, window)
        out = []
        for row in rows:
            if not operator and row.tenant_id is None:
                continue  # tenant admins never see the platform rollup
            out.append(
                {
                    "agent_key": row.agent_key,
                    "agent_version": row.agent_version,
                    "tenant_id": row.tenant_id,
                    "window": row.window,
                    "window_start": row.window_start.isoformat(),
                    "metrics": slo_mod.compute_metrics(row.counters),
                    "targets": row.targets,
                    "sample_n": row.sample_n,
                }
            )
        return out


def _fold(counters: dict, kind: str, payload: dict) -> None:
    if kind == "agent_run":
        slo_mod.fold_agent_run(counters, payload)
    elif kind == "proposal":
        slo_mod.fold_proposal(counters, payload)
    elif kind == "tool":
        slo_mod.fold_tool(counters, payload)
    elif kind == "token_usage":
        slo_mod.fold_token_usage(counters, payload)
