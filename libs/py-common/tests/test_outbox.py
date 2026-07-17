"""Real transactional-outbox relay integration: a row committed to a Postgres
outbox table is relayed to a real Kafka topic and marked published; the relay is
idempotent (a second pass relays nothing)."""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from windrose_common.kafka import KafkaConsumer, KafkaProducerClient
from windrose_common.outbox import OutboxRelay, OutboxTableSpec
from windrose_common.redisx import RedisDedupStore, build_redis

pytestmark = pytest.mark.integration

PG_URL = "postgresql+asyncpg://windrose:windrose_dev@localhost:5432/windrose"


async def test_relay_polls_postgres_and_publishes_to_kafka(postgres, kafka, redis_up, unique):
    table = f"pyc_outbox_{unique}"
    topic = f"pyc.outbox.{unique}"
    group = f"pyc-outbox-{unique}"
    engine = create_async_engine(PG_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    producer = KafkaProducerClient()
    await producer.start()
    redis = build_redis()
    dedup = RedisDedupStore(redis)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    f"CREATE TABLE {table} ("
                    "id bigserial PRIMARY KEY, tenant_id text, topic text, "
                    "payload jsonb, occurred_at timestamptz DEFAULT now(), "
                    "published_at timestamptz)"
                )
            )
            await conn.execute(
                sa.text(
                    f"INSERT INTO {table} (tenant_id, topic, payload) VALUES "
                    "(:t, :top, :p1), (:t, :top, :p2)"
                ),
                {
                    "t": f"tenant-{unique}",
                    "top": topic,
                    "p1": json.dumps(
                        {"event_id": f"o1-{unique}", "tenant_id": f"tenant-{unique}", "n": 1}
                    ),
                    "p2": json.dumps(
                        {"event_id": f"o2-{unique}", "tenant_id": f"tenant-{unique}", "n": 2}
                    ),
                },
            )

        spec = OutboxTableSpec(table=table, id_col="id", topic_col="topic", order_col="occurred_at")
        relay = OutboxRelay(session_factory, producer, spec)

        relayed = await relay.relay_once()
        assert relayed == 2
        # second pass: nothing left unpublished (idempotent)
        assert await relay.relay_once() == 0

        # the events really landed on Kafka
        received: list[dict] = []

        async def collect(env: dict) -> None:
            received.append(env)

        consumer = KafkaConsumer(topic, group, collect, dedup, producer)
        await consumer.start()
        try:
            stats = await consumer.consume_batch(max_messages=2, timeout_ms=4000)
        finally:
            await consumer.stop()
        assert stats.processed == 2
        assert {e["n"] for e in received} == {1, 2}
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
        await producer.stop()
        await redis.aclose()
        await engine.dispose()
