"""Real Kafka (Redpanda) round-trip for ai.proposal.v1 (ART-FR-046, MASTER-FR-031).
A proposal-created event published via the real idempotent producer is consumed
back with its master envelope intact."""

from __future__ import annotations

import uuid

import pytest

from app.container import build_container
from app.domain.entities import Run, new_uuid
from app.events.bus import KafkaEventBus
from app.graphs.base import WriteIntent
from tests.conftest import TENANT_A, make_settings

pytestmark = pytest.mark.integration


async def test_proposal_event_round_trip(require_kafka):
    from aiokafka import AIOKafkaConsumer

    topic = f"ai.proposal.v1.itest.{uuid.uuid4().hex[:8]}"
    bus = KafkaEventBus("localhost:9092")

    # patch the emit topic to our isolated test topic by publishing directly
    c = build_container(make_settings(), mode="memory", bus=bus)
    run = Run(run_id=new_uuid(), tenant_id=TENANT_A, session_id=new_uuid(),
              agent_key="case-triage", agent_version=1, temporal_workflow_id=None,
              status="running", principal_type="user_obo", obo_sub="u-77")
    await c.store.create_run(run)

    consumer = AIOKafkaConsumer(topic, bootstrap_servers="localhost:9092",
                               auto_offset_reset="earliest",
                               group_id=f"itest-{uuid.uuid4().hex[:6]}")
    await consumer.start()
    try:
        intent = WriteIntent(
            tool_id="case.apply_disposition", tool_version="1.0.0", tier="write-proposal",
            side_effects="reversible", args={"case_id": "c-1", "severity": "high"},
            rationale="r", affected_urns=[f"wr:{TENANT_A}:case:case/c-1"],
            predicted_effect={})
        # publish the same envelope shape the service emits, on the isolated topic
        from app.events.envelope import make_envelope, payload_of
        env = make_envelope(event_type="proposal.created", tenant_id=TENANT_A,
                            actor={"type": "agent", "id": "case-triage"},
                            resource_urn=f"wr:{TENANT_A}:agent:proposal/p-1",
                            payload={"tool_id": intent.tool_id,
                                     "affected_urns": intent.affected_urns})
        await bus.publish(topic, env)

        msg = await consumer.getone()
        import json
        got = json.loads(msg.value)
        assert got["event_type"] == "proposal.created"
        assert got["tenant_id"] == TENANT_A
        assert payload_of(got)["tool_id"] == "case.apply_disposition"
    finally:
        await consumer.stop()
        await bus.aclose()
