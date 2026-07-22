"""Master event envelope conformance (MASTER-FR-031/041, WS5 BRD 58).

Mirrors services/agent-runtime/tests/unit/test_envelope.py's
test_envelope_matches_go_envelope_fields: build a real envelope via this
service's own make_envelope() and check it against the SAME shared
datacern_common.events.validate_envelope every emitting service must satisfy.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.domain.urn import memory_urn
from app.events.envelope import make_envelope
from datacern_common.events import validate_envelope
from tests.conftest import TENANT_A


def test_envelope_matches_go_envelope_fields():
    env = make_envelope(
        event_type="memory.quarantined", tenant_id=TENANT_A,
        actor={"type": "user", "id": "u-1"},
        resource_urn=memory_urn(TENANT_A, "mem-1"),
        payload={"memory_id": "mem-1", "scope": "case", "source_type": "human_correction",
                 "merged": False, "classifier_score": 0.42},
        via_agent=None, trace_id="trace-1")
    validate_envelope(env)
    uuid.UUID(env["event_id"])
    uuid.UUID(env["tenant_id"])
    datetime.fromisoformat(env["occurred_at"])  # RFC3339-parseable
