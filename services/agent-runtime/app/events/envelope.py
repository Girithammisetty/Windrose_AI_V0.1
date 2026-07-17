"""Master event envelope (MASTER-FR-031). Partition key = tenant_id.

Wire shape mirrors libs/go-common/event/envelope.go EXACTLY: every Go consumer
(audit-service, usage-service, realtime-hub router) decodes
``Payload map[string]any`` — so ``payload`` MUST be a JSON OBJECT, never a
JSON-encoded string (a string payload fails decode and routes the event to the
DLQ, dropping it from the audit trail + usage metering).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any


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
    # Round-trip through JSON so non-serialisable values (datetimes, dataclass
    # leftovers) are coerced with default=str while the wire value stays an
    # OBJECT matching go-common event.Envelope.Payload (map[string]any).
    payload_obj: dict[str, Any] = json.loads(json.dumps(payload or {}, default=str))
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "tenant_id": tenant_id,
        "actor": actor,
        "via_agent": via_agent,
        "resource_urn": resource_urn,
        "occurred_at": datetime.now(UTC).isoformat(),
        "trace_id": trace_id,
        "payload": payload_obj,
    }


def payload_of(envelope: dict) -> dict[str, Any]:
    raw = envelope.get("payload")
    return json.loads(raw) if isinstance(raw, str) else (raw or {})
