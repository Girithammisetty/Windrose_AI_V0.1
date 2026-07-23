"""Master-envelope wire-contract conformance (MASTER-FR-031/041, BRD 58 WS5).

Mirrors services/agent-runtime/tests/unit/test_envelope.py: builds a real
envelope via this service's own app/events/envelope.py::make_envelope (the
wire-envelope builder _Base._emit uses in app/domain/services.py) and checks
it against the SAME shared datacern_common.events.validate_envelope both Go
and Python sides use.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from datacern_common.events import validate_envelope

from app.events.envelope import make_envelope
from tests.conftest import TENANT_A


def test_envelope_matches_shared_conformance_validator():
    """Real call shape from RunService._emit (app/domain/services.py):
    "experiment.created" with a user actor."""
    env = make_envelope(
        event_type="experiment.created", tenant_id=TENANT_A,
        actor={"type": "user", "id": "u1"},
        resource_urn=f"wr:{TENANT_A}:experiment:experiment/exp-1",
        payload={"experiment_id": "exp-1", "name": "fraud-xgb", "model_type": "classification",
                 "workspace_id": "ws-1"})
    validate_envelope(env)
    uuid.UUID(env["event_id"])
    uuid.UUID(env["tenant_id"])
    datetime.fromisoformat(env["occurred_at"])  # RFC3339-parseable
