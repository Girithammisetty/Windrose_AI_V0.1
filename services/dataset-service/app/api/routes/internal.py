"""Internal service-to-service endpoints (SPIFFE mTLS — MASTER-FR-014).

- POST /internal/v1/datasets/{id}/versions : version registration by
  ingestion/pipeline/inference (DST-FR-003).
- PUT /internal/v1/profiles/{id} : profiler result callback with HMAC body
  signature bound to the per-job single-use token (DST-FR-023) — replaces V1's
  unauthenticated set_profile.
"""

from __future__ import annotations

import hmac
import json
from hashlib import sha256

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth import require_internal
from app.api.idempotency import idempotent
from app.api.schemas import version_payload
from app.domain.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.services import CallCtx

router = APIRouter(prefix="/internal/v1")


def _service_ctx(request: Request, tenant_id: str, spiffe: str) -> CallCtx:
    service = spiffe.rsplit("/", 1)[-1] if spiffe else "unknown"
    return CallCtx(
        tenant_id=tenant_id,
        actor={"type": "service", "id": service},
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/datasets/{dataset_id}/versions", status_code=201)
async def register_version(
    request: Request,
    response: Response,
    dataset_id: str,
    spiffe: str = Depends(require_internal),
):
    c = request.app.state.container
    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError as exc:
        raise ValidationFailed("body must be JSON") from exc
    tenant_id = body.get("tenant_id")
    if not tenant_id:
        raise ValidationFailed("tenant_id is required on internal calls")
    ctx = _service_ctx(request, tenant_id, spiffe)

    async def work():
        version = await c.version_service.register(
            ctx,
            dataset_id,
            {
                "iceberg_snapshot_id": body["iceberg_snapshot_id"],
                "schema": body.get("schema") or {},
                "row_count": body.get("row_count"),
                "bytes": body.get("bytes"),
                "produced_by_urn": body.get("produced_by_urn"),
                "skip_profiling": body.get("skip_profiling", False),
            },
        )
        return 201, {"data": version_payload(version)}

    return await idempotent(request, response, c.deps.uow_factory, tenant_id, work)


@router.post("/mcp/invoke")
async def tool_facade(request: Request, spiffe: str = Depends(require_internal)):
    """BRD 56 inc2 — the governed MCP backend the tool-plane federates to when a
    four-eyes-approved ``dataset.entity.merge`` proposal executes. It confirms a
    reviewed merge candidate (link layer only; the SoR is never mutated,
    ER-FR-050/BR-4). Defense-in-depth: re-authorizes the DECIDER (obo_sub — the
    approver, per the proposal-execution contract) against the real OPA sidecar
    before applying, so a spoofed gateway call still can't confirm a merge the
    approver couldn't perform themselves."""
    from app.api.auth import Principal
    from app.domain.urn import dataset_urn

    c = request.app.state.container
    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError as exc:
        raise ValidationFailed("body must be JSON") from exc
    tool_id = body.get("tool_id")
    if tool_id != "dataset.entity.merge":
        raise NotFound(f"unknown tool_id {tool_id!r}")
    args = body.get("args") or {}
    tenant_id = body.get("tenant") or body.get("tenant_id")
    obo_sub = body.get("obo_sub")
    candidate_id = args.get("candidate_id")
    dataset_id = args.get("dataset_id")
    if not (tenant_id and obo_sub and candidate_id and dataset_id):
        raise ValidationFailed("tenant, obo_sub, args.candidate_id, args.dataset_id required")
    approve = bool(args.get("approve", True))

    # Re-check the effective human holds dataset.entity.merge on the dataset
    # (the actor performing the apply is the approver, ART execution contract).
    principal = Principal(
        sub=obo_sub, tenant_id=tenant_id, typ="user", scopes=[tool_id],
        workspace_id=args.get("workspace_id"))
    if not await request.app.state.authz.allow(
            principal, "dataset.entity.merge", dataset_urn(tenant_id, dataset_id)):
        return {"output": {"applied": False, "error": "not allowed: dataset.entity.merge"}}

    ctx = CallCtx(
        tenant_id=tenant_id, actor={"type": "user", "id": obo_sub},
        trace_id=getattr(request.state, "trace_id", None))
    result = await c.dataset_service.apply_entity_merge(
        tenant_id, candidate_id=candidate_id, decided_by=obo_sub, approve=approve, ctx=ctx)
    return {"output": {"applied": True, "candidate_id": candidate_id,
                       "status": result.get("status")}}


def _internal_tenant(request: Request) -> str:
    """Tenant for GET internal detail routes: the mesh-forwarded
    ``x-windrose-tenant-id`` header (semantic-service sends it — SEM-FR-002)."""
    tenant_id = request.headers.get("x-windrose-tenant-id")
    if not tenant_id:
        raise ValidationFailed("x-windrose-tenant-id header is required")
    return tenant_id


@router.get("/datasets/{dataset_id}")
async def internal_dataset_detail(
    request: Request,
    dataset_id: str,
    spiffe: str = Depends(require_internal),
):
    """Internal dataset detail for semantic-service binding validation
    (SEM-FR-002). Projects to {physical_table, schema {col->type}, primary_key}."""
    c = request.app.state.container
    tenant_id = _internal_tenant(request)
    schema, physical_table, primary_key = await c.dataset_service.dataset_detail(
        tenant_id, dataset_id
    )
    return {
        "data": {
            "physical_table": physical_table,
            "schema": schema,
            "primary_key": primary_key,
        }
    }


@router.get("/datasets/{dataset_id}/rows")
async def internal_dataset_rows(
    request: Request,
    dataset_id: str,
    limit: int = 10000,
    spiffe: str = Depends(require_internal),
):
    """Internal bulk row read for pipeline-orchestrator data inputs: a dataset's
    current-version rows from its pinned Iceberg snapshot (bounded by ``limit``,
    hard-capped). Returns {columns, rows}."""
    c = request.app.state.container
    tenant_id = _internal_tenant(request)
    limit = max(1, min(limit, 100_000))
    columns, rows = await c.dataset_service.read_rows(tenant_id, dataset_id, limit)
    return {"data": {"columns": columns, "rows": rows}}


@router.get("/datasets/{dataset_id}/profile")
async def internal_dataset_profile(
    request: Request,
    dataset_id: str,
    spiffe: str = Depends(require_internal),
):
    """Internal profile projection for semantic-service (SEM-FR-002). The column
    schema drives binding validation; top_values (per-column sample values from
    the latest completed profile's profile.json blob) drive sample-value
    validation (SEM-FR-080) and stay {} until a full profile has run."""
    c = request.app.state.container
    tenant_id = _internal_tenant(request)
    schema, _, _ = await c.dataset_service.dataset_detail(tenant_id, dataset_id)
    top_values = await c.profile_service.internal_top_values(tenant_id, dataset_id)
    return {"data": {"schema": schema, "top_values": top_values}}


@router.put("/profiles/{profile_id}")
async def profile_callback(
    request: Request,
    profile_id: str,
    spiffe: str = Depends(require_internal),
):
    c = request.app.state.container
    raw = await request.body()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("body must be JSON") from exc
    tenant_id = body.get("tenant_id")
    if not tenant_id:
        raise ValidationFailed("tenant_id is required on internal calls")

    # Signature verification: HMAC-SHA256 over the raw body with the per-job
    # single-use callback token issued at launch (DST-FR-023).
    async with c.deps.uow_factory(tenant_id) as uow:
        profile = await uow.profiles.get(profile_id)
    if profile is None:
        raise NotFound("profile not found")
    provided = request.headers.get("x-profiler-signature", "")
    expected = hmac.new(
        (profile.callback_token or "").encode(), raw, sha256
    ).hexdigest()
    if not profile.callback_token or not hmac.compare_digest(expected, provided):
        raise PermissionDenied("invalid profiler callback signature")

    ctx = _service_ctx(request, tenant_id, spiffe)
    updated = await c.profile_service.complete(ctx, profile_id, body)
    return {
        "data": {
            "profile_id": updated.id,
            "status": str(updated.status),
            "error_category": updated.error_category,
            "attempt": updated.attempt,
        }
    }
