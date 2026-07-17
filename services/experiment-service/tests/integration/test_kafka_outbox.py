"""Integration (real Redpanda/Kafka): the transactional outbox relay publishes a
committed event to a real Kafka topic and it is consumed back (MASTER-FR-034)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import TENANT_A
from tests.integration.conftest import KAFKA, require_port

pytestmark = pytest.mark.integration


async def test_outbox_relay_to_real_kafka(engine):
    require_port(9092, "Redpanda/Kafka")
    from windrose_common.kafka import KafkaProducerClient

    from app.events.bus import InMemoryDedupStore
    from app.events.envelope import make_envelope
    from app.store.sql import OutboxDispatcher, sql_uow_factory

    unique = uuid.uuid4().hex[:8]
    topic = f"experiment.events.v1.it{unique}"
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uow_factory = sql_uow_factory(session_factory)

    # 1) a committed state change writes an event to the outbox (same tx)
    envelope = make_envelope(
        event_type="model_version.promoted", tenant_id=TENANT_A,
        actor={"type": "user", "id": "u1"}, resource_urn=f"wr:{TENANT_A}:experiment:model/m",
        payload={"marker": unique})
    async with uow_factory(TENANT_A) as uow:
        await uow.outbox.add(topic, envelope)
        await uow.commit()

    # 2) the real Kafka producer + consumer round-trip via the relay
    from windrose_common.kafka import KafkaConfig, KafkaConsumer

    producer = KafkaProducerClient(KafkaConfig(bootstrap_servers=KAFKA))
    await producer.start()
    dispatcher_producer = KafkaProducerClient(KafkaConfig(bootstrap_servers=KAFKA))
    await dispatcher_producer.start()

    from windrose_common.kafka import KafkaEventBus

    bus = KafkaEventBus(dispatcher_producer)
    dispatcher = OutboxDispatcher(session_factory, bus)

    seen = []

    async def handler(env: dict) -> None:
        seen.append(env)

    consumer = KafkaConsumer(topic, f"exp-it-{unique}", handler, InMemoryDedupStore(),
                             producer, cfg=KafkaConfig(bootstrap_servers=KAFKA))
    await consumer.start()
    try:
        published = await dispatcher.run_once()
        assert published >= 1
        stats = await consumer.consume_batch(max_messages=1, timeout_ms=8000)
        assert stats.processed == 1
        assert seen[0]["payload"]["marker"] == unique
    finally:
        await consumer.stop()
        await producer.stop()
        await dispatcher_producer.stop()
