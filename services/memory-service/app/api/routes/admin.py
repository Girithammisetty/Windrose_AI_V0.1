"""Erasure, tenant policy, stats (MEM-FR-040/051/052)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.auth import get_principal
from app.api.schemas import ErasureIn, PolicyIn, data_envelope
from app.domain.errors import NotFound

router = APIRouter(prefix="/api/v1")

# Canonical action names (closed verb set per RBC-FR-022): reads use `read`,
# the policy PUT is an `update`, erasure kickoff is a `create`.
ERASURE_CREATE = "memory.erasure.create"
ERASURE_READ = "memory.erasure.read"
POLICY_READ = "memory.policy.read"
POLICY_UPDATE = "memory.policy.update"
STATS = "memory.stats.read"


async def _authz(request: Request, principal, action) -> None:
    if not await request.app.state.authz.allow(principal, action, None):
        from app.domain.errors import PermissionDenied
        raise PermissionDenied(f"missing permission {action}")


@router.post("/erasure", status_code=202)
async def start_erasure(request: Request, body: ErasureIn):
    principal = get_principal(request)
    await _authz(request, principal, ERASURE_CREATE)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    req = await request.app.state.container.erasure_service.start(
        ctx, body.subject_type, body.subject_id)
    return data_envelope({"operation_id": req.request_id, "status": req.status})


@router.get("/erasure/{request_id}")
async def get_erasure(request: Request, request_id: str):
    principal = get_principal(request)
    await _authz(request, principal, ERASURE_READ)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    req = await request.app.state.container.deps.store.get_erasure(ctx.tenant_id, request_id)
    if req is None:
        raise NotFound("erasure request not found")
    return data_envelope({"operation_id": req.request_id, "status": req.status,
                          "report": req.report,
                          "completed_at": req.completed_at.isoformat()
                          if req.completed_at else None})


@router.get("/policies/self")
async def get_policy(request: Request):
    principal = get_principal(request)
    await _authz(request, principal, POLICY_READ)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    p = await request.app.state.container.policy_service.get(ctx.tenant_id)
    return data_envelope({"ttl_overrides": p.ttl_overrides, "pii_classes": p.pii_classes,
                          "injection_profile": p.injection_profile,
                          "corpus_flags": p.corpus_flags})


@router.put("/policies/self")
async def put_policy(request: Request, body: PolicyIn):
    principal = get_principal(request)
    await _authz(request, principal, POLICY_UPDATE)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    p = await request.app.state.container.policy_service.put(ctx.tenant_id, body.model_dump())
    return data_envelope({"ttl_overrides": p.ttl_overrides, "pii_classes": p.pii_classes,
                          "injection_profile": p.injection_profile,
                          "corpus_flags": p.corpus_flags})


@router.get("/stats")
async def stats(request: Request):
    principal = get_principal(request)
    await _authz(request, principal, STATS)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    return data_envelope(await request.app.state.container.admin_service.stats(ctx))
