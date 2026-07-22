"""Master-envelope wire-contract test (MASTER-FR-031/041, BRD 58 WS5).

Pins ai-gateway's app.events.envelope.make_envelope against the SAME shared
datacern_common.events.validate_envelope every other emitting service (and
audit-service's Go consumer) checks against.
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
    env = make_envelope(
        event_type="ai.virtual_key.rotated",
        tenant_id=TENANT_A,
        actor={"type": "service", "id": "ai-gateway"},
        resource_urn=f"wr:{TENANT_A}:ai:virtual_key/vk-1",
        payload={"key_id": "vk-1", "principal_type": "user", "principal_id": "u-1"},
    )
    validate_envelope(env)
    uuid.UUID(env["event_id"])
    uuid.UUID(env["tenant_id"])
    datetime.fromisoformat(env["occurred_at"])  # RFC3339-parseable
