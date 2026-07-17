"""Master event envelope (MASTER-FR-031) for pipeline.events.v1."""

from __future__ import annotations

from app.utils import new_id, utcnow


def make_envelope(
    *,
    event_type: str,
    tenant_id: str,
    actor: dict,
    resource_urn: str,
    payload: dict,
    via_agent: dict | None = None,
    trace_id: str | None = None,
) -> dict:
    return {
        "event_id": new_id(),
        "event_type": event_type,
        "tenant_id": tenant_id,
        "actor": actor,
        "via_agent": via_agent,
        "resource_urn": resource_urn,
        "occurred_at": utcnow().isoformat(),
        "trace_id": trace_id or "",
        "payload": payload,
    }


def run_urn(tenant_id: str, run_id: str) -> str:
    return f"wr:{tenant_id}:pipeline:run/{run_id}"


def template_urn(tenant_id: str, template_id: str) -> str:
    return f"wr:{tenant_id}:pipeline:template/{template_id}"
