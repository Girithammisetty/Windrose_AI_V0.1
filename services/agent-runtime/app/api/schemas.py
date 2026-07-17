"""API view serializers (MASTER-FR-022 envelope: {data, page?})."""

from __future__ import annotations

from app.domain.entities import Proposal, Run, Session


def proposal_view(p: Proposal) -> dict:
    return {
        "id": p.proposal_id, "run_id": p.run_id, "agent_key": p.agent_key,
        "agent_version": p.agent_version, "tool_id": p.tool_id,
        "tool_version": p.tool_version, "tier": p.tier, "side_effects": p.side_effects,
        "args": p.args, "rationale": p.rationale, "affected_urns": p.affected_urns,
        # The primary resource this proposal targets (the bff's case-detail
        # Proposals tab groups by it). Proposals record the authoritative
        # resource reference as affected_urns (e.g. the triage graph puts the
        # case URN wr:{tenant}:case:case/{case_id} first).
        "resource_urn": p.affected_urns[0] if p.affected_urns else None,
        "predicted_effect": p.predicted_effect, "expires_at": p.expires_at.isoformat(),
        "status": p.status, "decision": p.decision,
        "created_at": p.created_at.isoformat(),
    }


def run_view(r: Run) -> dict:
    return {
        "id": r.run_id, "session_id": r.session_id, "agent_key": r.agent_key,
        "agent_version": r.agent_version, "status": r.status,
        "principal_type": r.principal_type, "temporal_workflow_id": r.temporal_workflow_id,
        "usage": r.usage, "error": r.error, "final_text": r.final_text,
        "created_at": r.created_at.isoformat(),
    }


def session_view(s: Session) -> dict:
    return {
        "id": s.session_id, "agent_key": s.agent_key, "agent_version": s.agent_version,
        "context_urn": s.context_urn, "status": s.status,
        "created_at": s.created_at.isoformat(),
        "last_activity_at": s.last_activity_at.isoformat(),
        "expires_hard_at": s.expires_hard_at.isoformat(),
    }
