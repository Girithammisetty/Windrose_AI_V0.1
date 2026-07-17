"""SLM transcript corpus — read API (distillation milestone 1).

Tenant-scoped (RLS) browse of the governed agent-run corpus that SFT curation
reads from. Sensitive fields are already PII-redacted at capture; isolation is
enforced by RLS on ``principal.tenant_id`` (same pattern as /proposals)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.auth import principal_of
from app.domain.errors import NotFound

router = APIRouter(prefix="/api/v1")


def _view(t) -> dict:
    return {
        "transcript_id": t.transcript_id, "run_id": t.run_id, "session_id": t.session_id,
        "agent_key": t.agent_key, "agent_version": t.agent_version,
        "principal_type": t.principal_type, "obo_sub": t.obo_sub,
        "inputs": t.inputs, "grounding": t.grounding, "final_text": t.final_text,
        "proposed_action": t.proposed_action, "proposal_id": t.proposal_id,
        "model": t.model, "usage": t.usage, "consent": t.consent,
        "decision": t.decision, "corrected_output": t.corrected_output,
        "decided_by": t.decided_by,
        "decided_at": t.decided_at.isoformat() if t.decided_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("/transcripts")
async def list_transcripts(
    request: Request,
    agent_key: str | None = Query(default=None, alias="filter[agent_key]"),
    only_decided: bool = Query(default=False, alias="filter[decided]"),
    limit: int = Query(default=50, ge=1, le=200),
):
    principal = await principal_of(request)
    c = request.app.state.container
    rows = await c.store.list_transcripts(
        principal.tenant_id, agent_key=agent_key, only_decided=only_decided, limit=limit)
    return {"data": [_view(t) for t in rows],
            "page": {"next_cursor": None, "has_more": False}}


@router.get("/transcripts/{transcript_id}")
async def get_transcript(request: Request, transcript_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    t = await c.store.get_transcript(principal.tenant_id, transcript_id)
    if t is None:
        raise NotFound("transcript not found")
    return {"data": _view(t)}
