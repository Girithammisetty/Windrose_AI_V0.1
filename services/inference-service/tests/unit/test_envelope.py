"""Master-envelope wire-contract conformance (MASTER-FR-031/041, BRD 58 WS5).

Mirrors services/agent-runtime/tests/unit/test_envelope.py: builds a real
envelope via this service's own app/events/envelope.py::make_envelope (the
wire-envelope builder used by app/events/consumer.py — NOT the CallCtx-shaped
wrapper in app/domain/services.py) and checks it against the SAME shared
datacern_common.events.validate_envelope both Go and Python sides use.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from datacern_common.events import validate_envelope

from app.events.envelope import make_envelope
from tests.conftest import TENANT_A


def test_envelope_matches_shared_conformance_validator():
    """Real call shape from app/events/consumer.py's DatasetEventHandler
    (inference.schedule.paused, actor={"type": "service", "id": "inference"})."""
    env = make_envelope(
        event_type="inference.schedule.paused", tenant_id=TENANT_A,
        actor={"type": "service", "id": "inference"},
        resource_urn=f"wr:{TENANT_A}:inference:schedule/sch-1",
        payload={"schedule_id": "sch-1", "name": "nightly-score", "enabled": False,
                 "paused_reason": "INPUT_DELETED"})
    validate_envelope(env)
    uuid.UUID(env["event_id"])
    uuid.UUID(env["tenant_id"])
    datetime.fromisoformat(env["occurred_at"])  # RFC3339-parseable
