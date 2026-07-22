"""Master-envelope wire-contract test (MASTER-FR-031/041, BRD 58 WS5).

Pins semantic-service's app.events.envelope.make_envelope against the SAME
shared datacern_common.events.validate_envelope every other emitting service
(and audit-service's Go consumer) checks against.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.events.envelope import make_envelope
from datacern_common.events import validate_envelope
from tests.conftest import TENANT_A


def test_envelope_matches_shared_conformance_validator():
    """Conformance with libs/py-common/datacern_common/events.py's
    validate_envelope, the same validator audit-service and agent-runtime
    check against (WS5, BRD 58) — not a duplicated ad hoc field check."""
    model_id = "018f0000-0000-7000-8000-00000000000c"
    env = make_envelope(
        event_type="model.created",
        tenant_id=TENANT_A,
        actor={"type": "user", "id": "u-1"},
        resource_urn=f"wr:{TENANT_A}:semantic:model/{model_id}",
        payload={"name": "sales_model", "workspace_id": "ws-1"},
        via_agent=None,
        trace_id="tr-1",
    )
    validate_envelope(env)
    uuid.UUID(env["event_id"])
    uuid.UUID(env["tenant_id"])
    datetime.fromisoformat(env["occurred_at"])  # RFC3339-parseable
