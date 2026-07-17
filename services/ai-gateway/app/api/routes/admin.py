"""Admin plane `/api/v1/admin/*` (AIG-FR-070): providers, ladders, budgets,
spend, virtual keys, guardrail policy, cache invalidation."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.auth import Principal, require, require_operator
from app.api.idempotency import idempotent
from app.domain.budgets import child_exceeds_parent_warning
from app.domain.entities import SCOPE_TYPES, WINDOWS, Budget
from app.domain.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    Unauthenticated,
    ValidationFailed,
)
from app.domain.guardrails import new_policy_version, validate_policy_doc
from app.events.envelope import make_envelope
from app.utils import uuid7

router = APIRouter(prefix="/api/v1/admin")


def _page_env(page, serializer) -> dict:
    return {
        "data": [serializer(x) for x in page.data],
        "page": {"next_cursor": page.next_cursor, "has_more": page.has_more},
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ------------------------------------------------------------------ providers


def _provider_dict(d) -> dict:
    return {
        "id": d.id, "provider": d.provider, "model_family": d.model_family,
        "deployment_name": d.deployment_name, "region": d.region, "cloud": d.cloud,
        "endpoint_vault_ref": d.endpoint_vault_ref, "tpm_limit": d.tpm_limit,
        "rpm_limit": d.rpm_limit, "priority": d.priority, "status": d.status,
        "created_at": _iso(d.created_at), "updated_at": _iso(d.updated_at),
    }


class ProviderCreate(BaseModel):
    provider: str
    model_family: str
    deployment_name: str
    region: str
    cloud: str
    endpoint_vault_ref: str
    tpm_limit: int = 0
    rpm_limit: int = 0
    priority: int = 100


class ProviderPatch(BaseModel):
    status: str | None = None
    priority: int | None = None
    tpm_limit: int | None = None
    rpm_limit: int | None = None
    endpoint_vault_ref: str | None = None
    reason: str | None = None


@router.get("/providers")
async def list_providers(request: Request, limit: int = Query(50, le=200),
                         cursor: str | None = None):
    await require_operator("ai.provider.read")(request)
    container = request.app.state.container
    page = await container.provider_admin.list(limit, cursor)
    body = _page_env(page, _provider_dict)
    for item in body["data"]:
        item["circuit_state"] = container.breaker.state_of(item["id"])
        item["healthy"] = container.health.healthy(item["id"])
    return body


@router.post("/providers", status_code=201)
async def create_provider(request: Request, body: ProviderCreate, response: Response):
    principal = await require_operator("ai.provider.write")(request)
    container = request.app.state.container

    async def work():
        d = await container.provider_admin.create(body.model_dump())
        return 201, {"data": _provider_dict(d)}

    return await idempotent(request, response, container.uow_factory,
                            principal.tenant_id, work)


@router.patch("/providers/{deployment_id}")
async def patch_provider(request: Request, deployment_id: str, body: ProviderPatch,
                         force: bool = False):
    await require_operator("ai.provider.write")(request)
    container = request.app.state.container
    d = await container.provider_admin.patch(
        deployment_id, body.model_dump(exclude_none=True), force=force
    )
    return {"data": _provider_dict(d)}


@router.post("/providers/{deployment_id}/drain")
async def drain_provider(request: Request, deployment_id: str, force: bool = False):
    await require_operator("ai.provider.write")(request)
    container = request.app.state.container
    d = await container.provider_admin.drain(deployment_id, force=force)
    return {"data": _provider_dict(d)}


# ------------------------------------------------------------------ ladders


class LadderPut(BaseModel):
    rungs: list[dict]
    max_rung: int | None = None
    scope: str = "platform"


def _ladder_dict(ladder) -> dict:
    return {
        "id": ladder.id, "request_class": ladder.request_class,
        "scope": ladder.scope, "rungs": ladder.rungs, "version": ladder.version,
        "max_rung": ladder.max_rung,
    }


@router.get("/ladders/{request_class}")
async def get_ladder(request: Request, request_class: str):
    principal = await require("ai.ladder.read")(request)
    container = request.app.state.container
    if request_class not in ("chat", "sql-gen", "judge", "embed"):
        raise ValidationFailed(f"unknown request class {request_class!r}")
    ladder = await container.ladder_service.resolve(principal.tenant_id, request_class)
    return {"data": _ladder_dict(ladder)}


@router.put("/ladders/{request_class}")
async def put_ladder(request: Request, request_class: str, body: LadderPut):
    container = request.app.state.container
    if request_class not in ("chat", "sql-gen", "judge", "embed"):
        raise ValidationFailed(f"unknown request class {request_class!r}")
    if body.scope == "platform":
        principal = await require_operator("ai.ladder.write")(request)
    else:
        principal = await require("ai.ladder.write")(request)
    ladder = await container.ladder_service.put(
        principal.tenant_id, request_class, body.scope, body.rungs,
        max_rung=body.max_rung,
    )
    await container.emit_event(principal.tenant_id, "ladder.updated", {
        "request_class": request_class, "scope": body.scope,
        "version": ladder.version,
    })
    # AIG-FR-043: ladder config change invalidates cache naturally via
    # context_hash; tenant overrides also flush the tenant's exact tier.
    if body.scope == "tenant":
        await container.cache.invalidate(principal.tenant_id)
    return {"data": _ladder_dict(ladder)}


# ------------------------------------------------------------------ budgets


class BudgetCreate(BaseModel):
    scope_type: str
    scope_ref: str
    window: str
    limit_usd: float = Field(ge=0)
    degrade_pct: int = Field(default=95, ge=1, le=100)


class BudgetPatch(BaseModel):
    limit_usd: float | None = Field(default=None, ge=0)
    degrade_pct: int | None = Field(default=None, ge=1, le=100)
    status: str | None = None


def _budget_dict(b: Budget) -> dict:
    return {
        "id": b.id, "scope_type": b.scope_type, "scope_ref": b.scope_ref,
        "window": b.window, "limit_usd": float(b.limit_usd),
        "degrade_pct": b.degrade_pct, "status": b.status,
        "created_at": _iso(b.created_at), "updated_at": _iso(b.updated_at),
    }


async def _budget_owner_tenant(container, principal: Principal, scope_type: str) -> str:
    if scope_type == "platform":
        return container.settings.platform_tenant_id
    return principal.tenant_id


@router.get("/budgets")
async def list_budgets(request: Request, limit: int = Query(50, le=200),
                       cursor: str | None = None,
                       scope_type: str | None = Query(None, alias="filter[scope_type]")):
    principal = await require("ai.budget.read")(request)
    container = request.app.state.container
    async with container.uow_factory(principal.tenant_id) as uow:
        page = await uow.budgets.list(limit, cursor, scope_type=scope_type)
    return _page_env(page, _budget_dict)


@router.get("/budgets/{budget_id}")
async def get_budget(request: Request, budget_id: str):
    principal = await require("ai.budget.read")(request)
    container = request.app.state.container
    async with container.uow_factory(principal.tenant_id) as uow:
        budget = await uow.budgets.get(budget_id)
    if budget is None:
        await container.cross_tenant_audit(principal, "budgets", budget_id)
        raise NotFound("budget not found")
    return {"data": _budget_dict(budget)}


@router.post("/budgets", status_code=201)
async def create_budget(request: Request, body: BudgetCreate, response: Response):
    principal = await require("ai.budget.write")(request)
    container = request.app.state.container
    if body.scope_type not in SCOPE_TYPES:
        raise ValidationFailed(f"scope_type must be one of {SCOPE_TYPES}")
    if body.window not in WINDOWS:
        raise ValidationFailed(f"window must be one of {WINDOWS}")
    if body.scope_type == "platform":
        authz = request.app.state.authz
        if not await authz.allow(principal, "ai.platform.admin", None):
            raise PermissionDenied("platform budgets require the platform operator")
    tenant_id = await _budget_owner_tenant(container, principal, body.scope_type)

    async def work():
        now = container.clock.now()
        budget = Budget(
            id=str(uuid7()), tenant_id=tenant_id, scope_type=body.scope_type,
            scope_ref=body.scope_ref, window=body.window, limit_usd=body.limit_usd,
            degrade_pct=body.degrade_pct, created_at=now, updated_at=now,
        )
        warnings = []
        async with container.uow_factory(tenant_id) as uow:
            existing = await uow.budgets.for_scope(body.scope_type, body.scope_ref)
            if any(b.window == body.window for b in existing):
                raise Conflict(
                    f"a {body.window} budget already exists for this scope"
                )
            parents = await uow.budgets.for_scope("tenant", tenant_id)
            warning = child_exceeds_parent_warning(budget, parents)
            if warning:
                warnings.append(warning)  # soft warning (AIG-FR-024)
            await uow.budgets.add(budget)
            await uow.commit()
        out = {"data": _budget_dict(budget)}
        if warnings:
            out["warnings"] = warnings
        return 201, out

    return await idempotent(request, response, container.uow_factory, tenant_id, work)


@router.patch("/budgets/{budget_id}")
async def patch_budget(request: Request, budget_id: str, body: BudgetPatch):
    principal = await require("ai.budget.write")(request)
    container = request.app.state.container
    async with container.uow_factory(principal.tenant_id) as uow:
        budget = await uow.budgets.get(budget_id)
        if budget is None:
            await container.cross_tenant_audit(principal, "budgets", budget_id)
            raise NotFound("budget not found")
        if body.limit_usd is not None:
            budget.limit_usd = body.limit_usd
        if body.degrade_pct is not None:
            budget.degrade_pct = body.degrade_pct
        if body.status is not None:
            if body.status not in ("active", "disabled"):
                raise ValidationFailed("status must be active|disabled")
            budget.status = body.status
        budget.updated_at = container.clock.now()
        await uow.budgets.update(budget)
        await uow.commit()
    return {"data": _budget_dict(budget)}


@router.delete("/budgets/{budget_id}", status_code=200)
async def delete_budget(request: Request, budget_id: str):
    principal = await require("ai.budget.write")(request)
    container = request.app.state.container
    async with container.uow_factory(principal.tenant_id) as uow:
        budget = await uow.budgets.get(budget_id)
        if budget is None:
            await container.cross_tenant_audit(principal, "budgets", budget_id)
            raise NotFound("budget not found")
        budget.deleted_at = container.clock.now()
        budget.status = "disabled"
        await uow.budgets.update(budget)
        await uow.commit()
    return {"data": _budget_dict(budget)}


@router.get("/spend")
async def live_spend(request: Request, scope_type: str, scope_ref: str,
                     window: str | None = None):
    principal = await require("ai.spend.read")(request)
    container = request.app.state.container
    tenant_id = await _budget_owner_tenant(container, principal, scope_type)
    async with container.uow_factory(tenant_id) as uow:
        budgets = await uow.budgets.for_scope(scope_type, scope_ref)
    tz_name, _ = "UTC", None
    async with container.uow_factory(principal.tenant_id) as uow:
        cfg = await uow.tenant_configs.get(principal.tenant_id)
        if cfg:
            tz_name = cfg.timezone
    rows = [
        await container.budget_engine.live_spend(b, tz_name)
        for b in budgets
        if window is None or b.window == window
    ]
    return {"data": rows}


@router.get("/spend/breakdown")
async def spend_breakdown(request: Request, window_hours: int = Query(24, ge=1, le=8760)):
    """Cost-detail breakdown over a window: REAL aggregation from the tenant's
    request_log (no fabricated numbers), rolled up by provider, by
    (provider, model), and by request-class. Provider + concrete model id are
    resolved from each row's deployment_id (deployment rows are platform-scoped,
    so the join happens here, not across the RLS boundary)."""
    principal = await require("ai.spend.read")(request)
    container = request.app.state.container
    since = container.clock.now() - timedelta(hours=window_hours)

    async with container.uow_factory(principal.tenant_id) as uow:
        rows = await uow.request_log.aggregate_costs(since)

    # Resolve deployment_id -> (provider, concrete model id) once per id.
    dep_ids = {r["deployment_id"] for r in rows if r.get("deployment_id")}
    dep_map: dict[str, tuple[str, str]] = {}
    for did in dep_ids:
        try:
            d = await container.provider_admin.get(did)
            dep_map[did] = (d.provider, d.deployment_name)
        except NotFound:
            dep_map[did] = ("unknown", "unknown")

    detail: list[dict] = []
    by_provider: dict[str, dict] = {}
    by_model: dict[tuple, dict] = {}
    by_class: dict[str, dict] = {}
    totals = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def _acc(bucket: dict, r: dict) -> None:
        bucket["requests"] += r["requests"]
        bucket["input_tokens"] += r["input_tokens"]
        bucket["output_tokens"] += r["output_tokens"]
        bucket["cost_usd"] = round(bucket["cost_usd"] + r["cost_usd"], 6)

    for r in rows:
        if r.get("cached"):
            provider, model = "(cached)", None
        elif r.get("deployment_id"):
            provider, model = dep_map.get(r["deployment_id"], ("unknown", "unknown"))
        else:
            provider, model = "(none)", None
        item = {
            "provider": provider, "model": model, "model_alias": r["model_alias"],
            "request_class": r["request_class"], "cached": r["cached"],
            "requests": r["requests"], "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cost_usd": round(r["cost_usd"], 6),
        }
        detail.append(item)
        _acc(totals, r)
        _acc(by_provider.setdefault(
            provider, {"provider": provider, "requests": 0, "input_tokens": 0,
                       "output_tokens": 0, "cost_usd": 0.0}), r)
        _acc(by_model.setdefault(
            (provider, model or r["model_alias"]),
            {"provider": provider, "model": model, "model_alias": r["model_alias"],
             "requests": 0, "input_tokens": 0, "output_tokens": 0,
             "cost_usd": 0.0}), r)
        _acc(by_class.setdefault(
            r["request_class"], {"request_class": r["request_class"], "requests": 0,
                                 "input_tokens": 0, "output_tokens": 0,
                                 "cost_usd": 0.0}), r)

    totals["cost_usd"] = round(totals["cost_usd"], 6)
    key = lambda x: x["cost_usd"]  # noqa: E731
    return {"data": {
        "window": {"since": _iso(since), "hours": window_hours,
                   "price_version": container.settings.price_version},
        "totals": totals,
        "by_provider": sorted(by_provider.values(), key=key, reverse=True),
        "by_model": sorted(by_model.values(), key=key, reverse=True),
        "by_request_class": sorted(by_class.values(), key=key, reverse=True),
        "detail": sorted(detail, key=key, reverse=True),
    }}


# ------------------------------------------------------------------ virtual keys


class KeyCreate(BaseModel):
    principal_type: str
    principal_id: str
    allowed_request_classes: list[str] | None = None
    max_rung: int = 2
    ttl_seconds: int | None = None
    tenant_id: str | None = None  # service-mint (SPIFFE) callers only


def _key_dict(k) -> dict:
    return {
        "id": k.id, "principal_type": k.principal_type, "principal_id": k.principal_id,
        "allowed_request_classes": k.allowed_request_classes, "max_rung": k.max_rung,
        "expires_at": _iso(k.expires_at), "status": k.status,
        "created_at": _iso(k.created_at),
    }


def _key_tenant(request: Request, body_tenant: str | None) -> str:
    """Tenant admin uses the JWT tenant; agent-runtime mints per-run keys via
    SPIFFE mTLS and must name the tenant explicitly (AIG-FR-032)."""
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return principal.tenant_id
    if getattr(request.state, "spiffe", None) and body_tenant:
        return body_tenant
    raise Unauthenticated("missing bearer token")


@router.post("/keys", status_code=201)
async def create_key(request: Request, body: KeyCreate, response: Response):
    container = request.app.state.container
    if getattr(request.state, "principal", None) is not None:
        await require("ai.key.write")(request)
    tenant_id = _key_tenant(request, body.tenant_id)

    async def work():
        key, secret = await container.key_service.create(
            tenant_id,
            principal_type=body.principal_type,
            principal_id=body.principal_id,
            allowed_request_classes=body.allowed_request_classes,
            max_rung=body.max_rung,
            ttl_seconds=body.ttl_seconds,
        )
        out = _key_dict(key)
        out["secret"] = secret  # shown once (AIG-FR-030)
        return 201, {"data": out}

    return await idempotent(request, response, container.uow_factory, tenant_id, work)


@router.get("/keys")
async def list_keys(request: Request, limit: int = Query(50, le=200),
                    cursor: str | None = None):
    principal = await require("ai.key.read")(request)
    container = request.app.state.container
    async with container.uow_factory(principal.tenant_id) as uow:
        page = await uow.keys.list(limit, cursor)
    return _page_env(page, _key_dict)


@router.post("/keys/{key_id}/revoke")
async def revoke_key(request: Request, key_id: str):
    principal = await require("ai.key.write")(request)
    container = request.app.state.container
    key = await container.key_service.revoke(principal.tenant_id, key_id)
    return {"data": _key_dict(key)}


@router.post("/keys/{key_id}/rotate")
async def rotate_key(request: Request, key_id: str):
    principal = await require("ai.key.write")(request)
    container = request.app.state.container
    key, secret = await container.key_service.rotate(principal.tenant_id, key_id)
    out = _key_dict(key)
    out["secret"] = secret
    return {"data": out}


# ------------------------------------------------------------------ guardrails


class GuardrailPut(BaseModel):
    policy: dict


@router.get("/guardrails")
async def get_guardrails(request: Request):
    principal = await require("ai.guardrail.read")(request)
    container = request.app.state.container
    policy = await container.guardrails.policy_for(principal.tenant_id)
    return {"data": {"policy": policy.policy, "version": policy.version}}


@router.put("/guardrails")
async def put_guardrails(request: Request, body: GuardrailPut):
    principal = await require("ai.guardrail.write")(request)
    container = request.app.state.container
    authz = request.app.state.authz
    operator_approved = await authz.allow(principal, "ai.platform.admin", None)
    validate_policy_doc(body.policy, operator_approved_off=operator_approved)
    async with container.uow_factory(principal.tenant_id) as uow:
        existing = await uow.policies.current()
        policy = new_policy_version(principal.tenant_id, existing, body.policy)
        policy.created_at = policy.updated_at = container.clock.now()
        policy = await uow.policies.put(policy)
        await uow.outbox.add(container.settings.events_topic, make_envelope(
            event_type="guardrail_policy.updated", tenant_id=principal.tenant_id,
            actor=principal.actor,
            resource_urn=f"wr:{principal.tenant_id}:ai:guardrail_policy/{policy.id}",
            payload={"version": policy.version},
        ))
        await uow.commit()
    # AIG-FR-043: guardrail-policy change invalidates the tenant cache
    await container.cache.invalidate(principal.tenant_id)
    return {"data": {"policy": policy.policy, "version": policy.version}}


# ------------------------------------------------------------------ cache


@router.delete("/cache")
async def invalidate_cache(request: Request, scope: str = "tenant",
                           workspace_id: str | None = None):
    principal = await require("ai.cache.invalidate")(request)
    container = request.app.state.container
    if scope not in ("tenant", "workspace"):
        raise ValidationFailed("scope must be tenant|workspace")
    if scope == "workspace" and not workspace_id:
        raise ValidationFailed("workspace scope requires workspace_id")
    purged = await container.cache.invalidate(
        principal.tenant_id, workspace_id if scope == "workspace" else None
    )
    return {"data": {"purged_entries": purged}}
