"""Integration: real Kafka (Redpanda) lifecycle events via the outbox relay, and
real OPA authz. Auto-skips when an endpoint is unreachable."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration.conftest import require_infra

pytestmark = pytest.mark.integration

TENANT = "11111111-1111-4111-8111-111111111111"


async def test_job_lifecycle_events_on_real_kafka(engine):
    """Outbox rows written by a real job transition are relayed to real Redpanda
    and read back by a real consumer (MASTER-FR-030/034, BRD §6)."""
    require_infra((9092, "Redpanda"), (5432, "Postgres"))
    from windrose_common.kafka import KafkaConfig, KafkaConsumer, KafkaProducerClient

    from app.events.bus import KafkaEventBus
    from app.store.sql import OutboxDispatcher, sql_uow_factory

    uid = uuid.uuid4().hex[:8]
    topic = f"inference.events.v1.it{uid}"
    uow_factory = sql_uow_factory(async_sessionmaker(engine, expire_on_commit=False))

    # write two lifecycle events into the outbox within a real tenant transaction
    async with uow_factory(TENANT) as uow:
        for et in ("inference.job.created", "inference.job.succeeded"):
            await uow.outbox.add(topic, {
                "event_id": str(uuid.uuid4()), "event_type": et, "tenant_id": TENANT,
                "resource_urn": f"wr:{TENANT}:inference:job/{uid}", "payload": {"job_id": uid}})

    # relay to real Kafka
    bus = KafkaEventBus()
    dispatcher = OutboxDispatcher(async_sessionmaker(engine, expire_on_commit=False), bus)
    published = await dispatcher.run_once()
    assert published == 2
    await bus.aclose()

    # consume back from real Kafka

    class Recorder:
        def __init__(self):
            self.types = []

        async def handle(self, envelope):
            self.types.append(envelope["event_type"])

    rec = Recorder()

    class _Dedup:
        async def already_processed(self, *_):
            return False

        async def mark_processed(self, *_):
            return None

    producer = KafkaProducerClient()
    await producer.start()
    consumer = KafkaConsumer(topic, f"it-{uid}", rec.handle, _Dedup(), producer,
                             cfg=KafkaConfig())
    await consumer.start()
    try:
        await consumer.consume_batch(max_messages=2, timeout_ms=8000)
    finally:
        await consumer.stop()
        await producer.stop()
    assert "inference.job.succeeded" in rec.types


async def test_opa_authz_allows_with_real_projection():
    """Real OPA + Redis: the OpaAuthzClient posts the granular projection and OPA
    returns allow for an admin subject (MASTER-FR-012)."""
    require_infra((8281, "OPA"), (6379, "Redis"))
    import json

    from windrose_common.projection import CATALOG_KEY
    from windrose_common.redisx import build_redis

    from app.api.auth import OpaAuthzClient, Principal

    uid = uuid.uuid4().hex[:8]
    tenant = f"tenant-{uid}"
    redis = build_redis()
    # seed the granular perm:* projection keys the loader reads
    await redis.set(CATALOG_KEY, json.dumps({"actions": {"inference.job.create": True}}))
    await redis.set(f"perm:{tenant}:u1:flags", json.dumps({"admin": True, "ws_admin": []}))
    ws = "33333333-3333-4333-8333-333333333333"
    client = OpaAuthzClient()
    principal = Principal(sub="u1", tenant_id=tenant, typ="user", scopes=[], workspace_id=ws)
    try:
        assert await client.allow(principal, "inference.job.create", None) is True
        # an unknown action is denied
        assert await client.allow(principal, "inference.job.nope", None) is False
    finally:
        await redis.delete(CATALOG_KEY, f"perm:{tenant}:u1:flags")
        await redis.aclose()
