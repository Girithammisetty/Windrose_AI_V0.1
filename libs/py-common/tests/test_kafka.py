"""Real Redpanda/Kafka integration: publish -> consume -> Redis dedup ->
DLQ-on-poison, exercising the actual wire protocol and a real consumer group."""

from __future__ import annotations

import pytest

from windrose_common.kafka import (
    ConsumeStats,
    KafkaConsumer,
    KafkaEventBus,
    KafkaProducerClient,
    dlq_topic,
)
from windrose_common.redisx import RedisDedupStore, build_redis

pytestmark = pytest.mark.integration


def _envelope(event_id: str, tenant: str, etype: str = "ingestion.completed") -> dict:
    return {
        "event_id": event_id,
        "event_type": etype,
        "tenant_id": tenant,
        "actor": {"type": "service", "id": "ingestion-service"},
        "resource_urn": f"wr:{tenant}:ingestion:ingestion/{event_id}",
        "occurred_at": "2026-07-10T00:00:00+00:00",
        "trace_id": "trace-1",
        "payload": {"ingestion_id": event_id},
    }


async def test_publish_consume_dedup_and_dlq(kafka, redis_up, unique):
    topic = f"pyc.events.{unique}"
    group = f"pyc-consumer-{unique}"
    tenant = f"t-{unique}"  # unique so Redis dedup keys never collide across runs
    evt_a, evt_b, evt_poison = f"evt-a-{unique}", f"evt-b-{unique}", f"evt-poison-{unique}"
    redis = build_redis()
    dedup = RedisDedupStore(redis)

    producer = KafkaProducerClient()
    await producer.start()
    bus = KafkaEventBus(producer)

    handled: list[str] = []

    async def handler(envelope: dict) -> None:
        if envelope["payload"]["ingestion_id"] == "poison":
            raise RuntimeError("boom — permanent handler failure")
        handled.append(envelope["event_id"])

    consumer = KafkaConsumer(
        topic, group, handler, dedup, producer,
        max_retries=2, backoff_base_s=0.01, backoff_cap_s=0.02,
    )
    await consumer.start()
    try:
        # two distinct events + one duplicate of the first + one poison event
        await bus.publish(topic, _envelope(evt_a, tenant))
        await bus.publish(topic, _envelope(evt_b, tenant))
        await bus.publish(topic, _envelope(evt_a, tenant))  # duplicate event_id -> deduped
        poison = _envelope(evt_poison, tenant)
        poison["payload"]["ingestion_id"] = "poison"  # handler raises on this -> DLQ
        await bus.publish(topic, poison)

        stats: ConsumeStats = await consumer.consume_batch(max_messages=4, timeout_ms=8000)

        assert stats.processed == 2  # evt-a, evt-b
        assert stats.deduped == 1  # second evt-a
        assert stats.dlq == 1  # poison routed to DLQ after retries
        assert sorted(handled) == sorted([evt_a, evt_b])

        # the DLQ topic actually received the poisoned message
        dlq_consumer = KafkaConsumer(
            dlq_topic(topic, group), f"{group}-dlqcheck", handler, dedup, producer
        )
        await dlq_consumer.start()
        try:
            seen: list[dict] = []

            async def collect(env: dict) -> None:
                seen.append(env)

            dlq_consumer.handler = collect
            dlq_stats = await dlq_consumer.consume_batch(max_messages=2, timeout_ms=3000)
            assert dlq_stats.processed >= 1
        finally:
            await dlq_consumer.stop()
    finally:
        await consumer.stop()
        await producer.stop()
        await redis.aclose()
