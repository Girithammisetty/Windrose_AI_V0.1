"""Master event envelope conformance (MASTER-FR-031/041).

Every Python service builds its own envelope dict (there's no shared
constructor — see e.g. services/agent-runtime/app/events/envelope.py's
``make_envelope``), but they must all produce the SAME wire shape
libs/go-common/event/envelope.go's ``Envelope`` decodes. ``validate_envelope``
is the single, shared conformance check (WS5, BRD 58: event-envelope
conformance as a CI gate) any service's test suite can call against its own
``make_envelope`` output, mirroring the Go side's ``event.Validate`` and
audit-service's consumption-side ``ValidateEnvelope`` exactly.
"""

from __future__ import annotations

MASTER_ACTOR_TYPES = {"user", "service", "agent", "platform"}


def validate_envelope(envelope: dict) -> None:
    """Raise ValueError if envelope violates the master contract."""
    missing = []
    if not envelope.get("event_id"):
        missing.append("event_id")
    if not envelope.get("event_type"):
        missing.append("event_type")
    if not envelope.get("tenant_id"):
        missing.append("tenant_id")
    actor = envelope.get("actor") or {}
    if not actor.get("type") or not actor.get("id"):
        missing.append("actor")
    if not envelope.get("occurred_at"):
        missing.append("occurred_at")
    if missing:
        raise ValueError(f"envelope invalid: missing/invalid {','.join(missing)}")

    actor_type = actor.get("type")
    if actor_type not in MASTER_ACTOR_TYPES:
        raise ValueError(f"envelope invalid: actor.type {actor_type!r} not allowed")

    if not isinstance(envelope.get("payload"), dict):
        raise ValueError("envelope invalid: payload must be a JSON object")
