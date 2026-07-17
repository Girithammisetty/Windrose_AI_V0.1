"""Chat + sessions + runs (ART-FR-070/020, §5)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from app.adapters.rbac import fetch_caller_context
from app.api.auth import principal_of
from app.api.schemas import run_view, session_view
from app.domain.entities import now
from app.domain.errors import NotFound, ValidationFailed
from app.runtime.orchestrator import Orchestrator

router = APIRouter(prefix="/api/v1")


def _inputs_from_body(body: dict, tenant_id: str) -> dict:
    meta = body.get("metadata") or {}
    inputs: dict = {"tenant_id": tenant_id}
    messages = body.get("messages") or []
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if user_msgs:
        inputs["query"] = user_msgs[-1]
    if meta.get("case_id"):
        inputs["case_id"] = meta["case_id"]
    ctx = meta.get("context_urn")
    if ctx and ":case/" in ctx:
        inputs["case_id"] = ctx.split(":case/")[-1]
    inputs.update(meta.get("inputs") or {})
    return inputs


@router.post("/agents/{agent_key}/chat/completions")
async def chat_completions(request: Request, agent_key: str, body: dict = Body(...)):
    principal = await principal_of(request)
    c = request.app.state.container
    orch = Orchestrator(c)
    meta = body.get("metadata") or {}

    session = await orch.get_or_create_session(
        tenant_id=principal.tenant_id, user_id=principal.sub, agent_key=agent_key,
        session_id=meta.get("session_id"), context_urn=meta.get("context_urn"))

    inputs = _inputs_from_body(body, principal.tenant_id)
    # Thread the caller's workspace so workspace-scoped grounding reads (e.g. the
    # dashboard-designer's semantic-layer metrics/dimensions) resolve the right
    # published models. Harmless for agents that ignore it.
    if getattr(principal, "workspace_id", None):
        inputs.setdefault("workspace_id", principal.workspace_id)
    # Role-ground the copilot (ART-FR-040): resolve the CALLER's roles from rbac
    # with their own token so graphs can tailor persona/tone to the invoking user
    # (adjuster vs data scientist). Best-effort — a failed lookup degrades to the
    # tenant-level persona and never blocks the run.
    caller = await fetch_caller_context(
        c.settings.rbac_service_url, request.headers.get("authorization", ""))
    if caller:
        inputs["caller"] = caller
    if agent_key == "case-triage" and "case_id" not in inputs:
        raise ValidationFailed("case-triage requires metadata.case_id or a case context_urn")

    run, result = await orch.start_run(
        principal=principal, agent_key=agent_key, inputs=inputs, session=session)

    headers = {
        "x-windrose-stream-topic": f"agent_run:{run.run_id}",
        "x-windrose-ai-generated": "true",
    }
    payload = {"data": {"run_id": run.run_id, "session_id": session.session_id,
                        "agent_version": session.agent_version, **result}}
    return JSONResponse(content=payload, headers=headers)


@router.post("/sessions")
async def create_session(request: Request, body: dict = Body(default={})):
    principal = await principal_of(request)
    c = request.app.state.container
    agent_key = body.get("agent_key")
    if not agent_key:
        raise ValidationFailed("agent_key required")
    orch = Orchestrator(c)
    session = await orch.get_or_create_session(
        tenant_id=principal.tenant_id, user_id=principal.sub, agent_key=agent_key,
        session_id=None, context_urn=body.get("context_urn"))
    return {"data": session_view(session)}


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    s = await c.store.get_session(principal.tenant_id, session_id)
    if s is None:
        raise NotFound("session not found")
    return {"data": session_view(s)}


@router.post("/sessions/{session_id}/terminate")
async def terminate_session(request: Request, session_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    s = await c.store.get_session(principal.tenant_id, session_id)
    if s is None:
        raise NotFound("session not found")
    s.status = "terminated"
    s.last_activity_at = now()
    await c.store.update_session(s)
    return {"data": session_view(s)}


@router.get("/runs")
async def list_runs(
    request: Request,
    agent_key: str | None = Query(default=None, alias="filter[agent_key]"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Run history for the caller's tenant (Tier 2b browse surface), newest
    first. Tenant scoping comes from the verified token (RLS in the SQL store);
    any tenant principal may list — the same visibility as GET /runs/{id},
    which was already open to every principal in the tenant."""
    principal = await principal_of(request)
    c = request.app.state.container
    rows = await c.store.list_runs(principal.tenant_id, agent_key=agent_key, limit=limit)
    # Mirrors list_proposals' page envelope: no cursor pagination downstream yet.
    return {"data": [run_view(r) for r in rows],
            "page": {"next_cursor": None, "has_more": False}}


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    r = await c.store.get_run(principal.tenant_id, run_id)
    if r is None:
        raise NotFound("run not found")
    return {"data": run_view(r)}


@router.get("/runs/{run_id}/trace")
async def get_run_trace(request: Request, run_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    r = await c.store.get_run(principal.tenant_id, run_id)
    if r is None:
        raise NotFound("run not found")
    checkpoints = await c.store.load_checkpoints(run_id)
    trace = []
    for cp in checkpoints:
        trace.extend((cp.get("state_ref") or {}).get("trace", []))
    return {"data": {"run_id": run_id, "status": r.status, "trace": trace,
                     "usage": r.usage}}
