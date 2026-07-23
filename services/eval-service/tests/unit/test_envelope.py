"""Master event envelope conformance (MASTER-FR-031/041, WS5 BRD 58).

Mirrors services/agent-runtime/tests/unit/test_envelope.py's
test_envelope_matches_go_envelope_fields: build a real envelope via this
service's own make_envelope() and check it against the SAME shared
datacern_common.events.validate_envelope every emitting service must satisfy.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from datacern_common.events import validate_envelope

from app.events.envelope import make_envelope
from tests.conftest import TENANT_A


def test_envelope_matches_go_envelope_fields():
    env = make_envelope(
        event_type="dataset.version_frozen", tenant_id=TENANT_A,
        actor={"type": "user", "id": "u-1"},
        resource_urn=f"wr:{TENANT_A}:dataset/ds-1@1",
        payload={"dataset_key": "ds-1", "version": 1, "case_count": 12},
        via_agent=None, trace_id="trace-1")
    validate_envelope(env)
    uuid.UUID(env["event_id"])
    uuid.UUID(env["tenant_id"])
    datetime.fromisoformat(env["occurred_at"])  # RFC3339-parseable
