"""Run lifecycle events reach REAL Redpanda via the transactional outbox relay, and
labeled datasets are assembled from REAL case.disposition_applied Kafka events."""

from __future__ import annotations

import uuid

import pytest

from app.container import build_container
from app.domain.entities import CallCtx
from app.events.bus import KafkaOutboxBus
from app.events.consumer import KafkaPipelineConsumer
from app.events.envelope import make_envelope
from app.store.sql import OutboxDispatcher
from tests.conftest import TENANT_A, WORKSPACE, FakeExecutor, FakeMlflow, make_settings
from tests.integration.conftest import KAFKA, kafka_up

pytestmark = pytest.mark.integration


async def _training_template(c, ctx):
    template, _ = await c.instantiation_service.instantiate_pipeline(
        ctx, "random_forest", mode="train",
        dataset_refs={"TRAIN": "wr:t:dataset:dataset/x"}, params={},
        workspace_id=WORKSPACE, name=f"rf-{uuid.uuid4().hex[:6]}")
    return template


async def test_run_lifecycle_events_on_real_kafka(app_sf, clock):
    if not kafka_up():
        pytest.skip(f"Redpanda unreachable at {KAFKA}")
    from windrose_common.kafka import KafkaConfig, KafkaProducerClient

    topic = f"pipeline.events.v1.it-{uuid.uuid4().hex[:8]}"
    settings = make_settings(events_topic=topic, default_min_seconds_between_runs=0)
    c = build_container(settings, mode="sql", session_factory=app_sf, clock=clock,
                        executor=FakeExecutor(), mlflow=FakeMlflow())
    ctx = CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "u"},
                  workspace_id=WORKSPACE)
    template = await _training_template(c, ctx)
    _, run = await c.run_service.create_run(
        ctx, template.id, {"training_data": [{"a": 1, "label": "x"}, {"a": 2, "label": "y"}]})
    await c.run_service.drive_run(TENANT_A, run.id)

    # Relay the outbox rows to REAL Kafka.
    producer = KafkaProducerClient(KafkaConfig(bootstrap_servers=KAFKA))
    await producer.start()
    dispatcher = OutboxDispatcher(app_sf, KafkaOutboxBus(producer))
    published = 0
    for _ in range(5):
        published += await dispatcher.run_once()

    # Consume them back from the real broker.
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        topic, bootstrap_servers=KAFKA, group_id=f"it-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest", enable_auto_commit=False)
    await consumer.start()
    seen = set()
    try:
        import json
        import time

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(seen) < 4:
            batch = await consumer.getmany(timeout_ms=1000, max_records=50)
            for _tp, msgs in batch.items():
                for m in msgs:
                    seen.add(json.loads(m.value)["event_type"])
    finally:
        await consumer.stop()
        await producer.stop()

    assert "pipeline.run.submitted" in seen
    assert "pipeline.run.started" in seen
    assert "pipeline.run.succeeded" in seen


async def test_labeled_dataset_from_real_disposition_kafka(app_sf, clock):
    if not kafka_up():
        pytest.skip(f"Redpanda unreachable at {KAFKA}")
    from windrose_common.kafka import KafkaConfig, KafkaProducerClient

    c = build_container(make_settings(), mode="sql", session_factory=app_sf, clock=clock)
    urn = f"wr:{TENANT_A}:dataset:dataset/kafka-claims"
    case_id = uuid.uuid4().hex
    env = make_envelope(
        event_type="case.disposition_applied", tenant_id=TENANT_A,
        actor={"type": "user", "id": "analyst"},
        resource_urn=f"wr:{TENANT_A}:case:case/{case_id}",
        payload={"dataset_urn": urn, "row_pk": case_id,
                 "disposition": {"id": "d", "code": "DUP", "category": "duplicate"},
                 "features": {"amount": 500, "vendor_repeat": 1}})

    topic = f"case.events.v1.it-{case_id}"
    producer = KafkaProducerClient(KafkaConfig(bootstrap_servers=KAFKA))
    await producer.start()
    await producer.send(topic, TENANT_A, env)

    runner = KafkaPipelineConsumer(topic, c.consumer, producer,
                                   group_id=f"it-{case_id}", bootstrap_servers=KAFKA)
    await runner.start()
    try:
        await runner.consume_batch(1, timeout_ms=15000)
    finally:
        await runner.stop()
        await producer.stop()

    async with c.deps.uow_factory(TENANT_A) as uow:
        rows = await uow.labeled_examples.list_for_dataset(urn)
    assert any(r.row_pk == case_id and r.label == "duplicate" for r in rows), \
        "disposition event was not assembled into a labeled example from Kafka"
