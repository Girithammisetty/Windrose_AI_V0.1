"""Event envelope (MASTER-FR-031). Partition key = tenant_id."""

from __future__ import annotations

from app.utils import utcnow, uuid7


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
        "event_id": str(uuid7()),
        "event_type": event_type,
        "tenant_id": tenant_id,
        "actor": actor,
        "via_agent": via_agent,
        "resource_urn": resource_urn,
        "occurred_at": utcnow().isoformat(),
        "trace_id": trace_id,
        "payload": payload,
    }
