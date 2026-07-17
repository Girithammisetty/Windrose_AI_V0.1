"""Master-envelope wire-contract tests (MASTER-FR-031).

Every Go consumer (audit-service domain.Envelope, usage-service ingest,
libs/go-common/event/envelope.go) decodes ``payload`` as ``map[string]any`` —
a JSON OBJECT. A JSON-encoded STRING payload fails decode and routes the event
to the DLQ, which is exactly the regression these tests pin against. They also
pin the semantic event_type usage-service meters on (``agent_run.completed``
with payload.status == "succeeded").
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.container import build_container
from app.domain.entities import Run, new_uuid
from app.events.envelope import make_envelope, payload_of
from tests.conftest import TENANT_A, make_settings


def test_envelope_payload_is_an_object_not_a_string():
    env = make_envelope(
        event_type="agent_run.completed", tenant_id=TENANT_A,
        actor={"type": "user", "id": "u-1"}, resource_urn="wr:t:agent:run/r-1",
        payload={"run_id": "r-1", "status": "succeeded"})
    assert isinstance(env["payload"], dict), "payload must be a JSON object on the wire"
    # And it must survive a JSON round trip as an object (what Kafka carries).
    wire = json.loads(json.dumps(env, default=str))
    assert isinstance(wire["payload"], dict)
    assert wire["payload"]["run_id"] == "r-1"


def test_envelope_matches_go_envelope_fields():
    """Field-for-field conformance with libs/go-common/event/envelope.go +
    audit-service ValidateEnvelope (event_id/tenant_id parse as UUIDs,
    occurred_at parses, actor.type in the allowed set)."""
    env = make_envelope(
        event_type="agent_run.completed", tenant_id=TENANT_A,
        actor={"type": "user", "id": "u-1"}, resource_urn="wr:t:agent:run/r-1",
        payload={"a": 1}, via_agent={"agent_id": "case-triage", "version": "1"})
    for f in ("event_id", "event_type", "tenant_id", "actor", "via_agent",
              "resource_urn", "occurred_at", "trace_id", "payload"):
        assert f in env, f"missing envelope field {f}"
    uuid.UUID(env["event_id"])
    uuid.UUID(env["tenant_id"])
    datetime.fromisoformat(env["occurred_at"])  # RFC3339-parseable
    assert env["actor"]["type"] in ("user", "service", "agent", "platform")
    assert env["via_agent"] == {"agent_id": "case-triage", "version": "1"}


def test_envelope_coerces_unserialisable_values_but_stays_object():
    env = make_envelope(
        event_type="x", tenant_id=TENANT_A, actor={"type": "service", "id": "s"},
        resource_urn="urn", payload={"when": datetime(2026, 7, 11, 12, 0)})
    assert isinstance(env["payload"], dict)
    assert isinstance(env["payload"]["when"], str)
    assert payload_of(env) == env["payload"]


async def test_emit_run_uses_semantic_event_type_and_succeeded_status():
    """usage-service (internal/ingest/mapping.go) meters agent tasks only for
    event_type == "agent_run.completed" AND payload.status == "succeeded"; the
    topic stays ai.agent_run.v1."""
    c = build_container(make_settings(), mode="memory")
    run = Run(run_id=new_uuid(), tenant_id=TENANT_A, session_id=new_uuid(),
              agent_key="analytics", agent_version=1, temporal_workflow_id=None,
              status="completed", principal_type="user_obo", obo_sub="u-1")
    await c.store.create_run(run)
    await c.run_engine.emit_run(run, "agent_run.completed")

    assert len(c.bus.published) == 1
    topic, env = c.bus.published[0]
    assert topic == "ai.agent_run.v1"
    assert env["event_type"] == "agent_run.completed"
    assert isinstance(env["payload"], dict)
    assert env["payload"]["status"] == "succeeded"  # metering filter match
    assert env["payload"]["run_status"] == "completed"
    # ... and the same envelope landed in the transactional outbox.
    assert c.store.outbox[-1]["payload"]["event_type"] == "agent_run.completed"
    assert isinstance(c.store.outbox[-1]["payload"]["payload"], dict)
