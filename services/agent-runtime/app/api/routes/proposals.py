"""Proposal APIs (ART-FR-073): approval inbox + idempotent decide.

Decide is first-wins (BR-12): later attempts on a decided proposal return 409
CONFLICT with the winning decision. On approve/edit, if the run is Temporal-backed
the workflow executes the signed grant; otherwise execution runs inline."""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request

from app.api.auth import principal_of
from app.api.schemas import proposal_view
from app.domain.entities import now
from app.domain.errors import NotFound, ValidationFailed

router = APIRouter(prefix="/api/v1")


@router.get("/proposals")
async def list_proposals(
    request: Request,
    status: str | None = Query(default=None, alias="filter[status]"),
    agent_key: str | None = Query(default=None, alias="filter[agent_key]"),
    resource_urn: str | None = Query(default=None, alias="filter[resource_urn]"),
    limit: int = Query(default=50, ge=1, le=200),
):
    principal = await principal_of(request)
    c = request.app.state.container
    # The bff joins multiple URNs with commas (proposalsByResourceUrns); match
    # proposals whose affected_urns overlap ANY of them.
    urns = [u for u in (resource_urn or "").split(",") if u.strip()] or None
    rows = await c.store.list_proposals(principal.tenant_id, status=status,
                                        agent_key=agent_key, resource_urns=urns,
                                        limit=limit)
    return {"data": [proposal_view(p) for p in rows],
            "page": {"next_cursor": None, "has_more": False}}


@router.get("/proposals/{proposal_id}")
async def get_proposal(request: Request, proposal_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    p = await c.store.get_proposal(principal.tenant_id, proposal_id)
    if p is None:
        raise NotFound("proposal not found")
    return {"data": proposal_view(p)}


@router.post("/proposals/{proposal_id}/decide")
async def decide_proposal(request: Request, proposal_id: str, body: dict = Body(...)):
    principal = await principal_of(request)
    c = request.app.state.container
    action = body.get("action")
    if action not in ("approve", "reject", "edit_args", "respond"):
        raise ValidationFailed("action must be approve|reject|edit_args|respond")

    # self-approval policy from tenant config
    p = await c.store.get_proposal(principal.tenant_id, proposal_id)
    if p is None:
        raise NotFound("proposal not found")
    cfg = await c.store.get_tenant_config(principal.tenant_id, p.agent_key)
    self_ok = bool(cfg and cfg.self_approval)

    temporal_backed = await _is_temporal_backed(c, principal.tenant_id, p.run_id)
    decided = await c.proposal_service.decide(
        tenant_id=principal.tenant_id, proposal_id=proposal_id, actor_sub=principal.sub,
        action=action, message=body.get("message"), edited_args=body.get("edited_args"),
        self_approval_allowed=self_ok, execute=not temporal_backed)

    if temporal_backed and decided.status in ("approved", "edited_approved"):
        await _signal_workflow(c, p.run_id, decided, principal.sub)

    return {"data": proposal_view(decided)}


async def _is_temporal_backed(c, tenant_id: str, run_id: str) -> bool:
    if not c.settings.use_temporal:
        return False
    run = await c.store.get_run(tenant_id, run_id)
    return bool(run and run.temporal_workflow_id)


async def _signal_workflow(c, run_id: str, decided, decided_by: str) -> None:
    run = await c.store.get_run(decided.tenant_id, run_id)
    handle = c.extras["temporal_client"].get_workflow_handle(run.temporal_workflow_id)
    args = decided.decision.get("edited_args") if decided.decision else None
    await handle.signal("proposal_decision", {
        "action": decided.decision["action"], "decided_by": decided_by,
        "args": args or decided.args, "decided_at": now().isoformat()})
