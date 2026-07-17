"""Integration: dataset-service's REAL runtime adapters against live dev infra
(MinIO blob store, Iceberg REST catalog, Redpanda + Redis consumer, OPA). Proves
the stub-removal wiring speaks the real wire protocol. Auto-skips when an
endpoint is unreachable."""

from __future__ import annotations

import socket
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def _reachable(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=1.0):
            return True
    except OSError:
        return False


def _require(port: int, name: str) -> None:
    if not _reachable(port):
        pytest.skip(f"{name} not reachable on localhost:{port} — dev infra down")


async def test_s3_blob_store_put_get_presigned():
    _require(9000, "MinIO")
    from app.adapters.object_store import S3ObjectStore

    store = S3ObjectStore("windrose-profiles")
    key = f"dst-it/{uuid.uuid4().hex[:8]}/profile.json"
    body = b'{"rows": 7}'
    await store.put(key, body, "application/json")
    assert await store.get(key) == body
    url = await store.signed_url(key, ttl_hours=1)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    assert resp.status_code == 200 and resp.content == body
    await store.delete(key)
    assert await store.exists(key) is False


async def test_iceberg_rest_catalog_reads_snapshot():
    _require(8181, "Iceberg REST")
    _require(9000, "MinIO")
    from windrose_common.iceberg import IcebergTableWriter, RowBatch

    from app.adapters.catalog import IcebergRestCatalog

    table = f"bronze.dstit{uuid.uuid4().hex[:8]}.ds_x"
    catalog = IcebergRestCatalog()
    writer = IcebergTableWriter()

    async def batches():
        yield RowBatch(columns=["a", "b"], rows=[["1", "2"], ["3", "4"]])

    try:
        staged = await writer.stage(table, batches(), {"ingestion_id": "i1", "source": "upload"})
        result = await writer.commit(staged)
        assert await catalog.snapshot_exists(table, result.snapshot_id) is True
        assert await catalog.snapshot_exists(table, 999999999) is False
        df = await catalog.read_snapshot(table, result.snapshot_id)
        assert len(df) == 2
    finally:
        await catalog.drop_table(table)


async def test_kafka_consumer_dedup_via_redis():
    _require(9092, "Redpanda")
    _require(6379, "Redis")
    from windrose_common.kafka import KafkaProducerClient

    from app.events.bus import KafkaEventBus, RedisDedupStore
    from app.events.consumer import KafkaIngestionConsumer

    unique = uuid.uuid4().hex[:8]
    topic = f"ingestion.events.v1.dstit{unique}"

    class RecordingHandler:
        def __init__(self):
            self.seen: list[str] = []

        async def handle(self, envelope: dict) -> None:
            self.seen.append(envelope["event_id"])

    handler = RecordingHandler()
    dedup = RedisDedupStore()
    producer = KafkaProducerClient()
    await producer.start()
    bus = KafkaEventBus()
    consumer = KafkaIngestionConsumer(handler, dedup, producer, topic=topic)
    consumer._consumer.group_id = f"dst-it-{unique}"
    await consumer.start()
    try:
        env = {
            "event_id": f"e-{unique}",
            "event_type": "ingestion.completed",
            "tenant_id": "t-1",
            "payload": {"ingestion_id": "i1"},
        }
        await bus.publish(topic, env)
        await bus.publish(topic, dict(env))  # duplicate event_id
        stats = await consumer.consume_batch(max_messages=2, timeout_ms=4000)
        assert stats.processed == 1
        assert stats.deduped == 1
        assert handler.seen == [f"e-{unique}"]
    finally:
        await consumer.stop()
        await producer.stop()
        await bus.aclose()
        await dedup.aclose()


async def test_opa_authz_client_allows_with_projection():
    """OpaAuthzClient assembles the projection from the granular rbac perm:*
    keys (windrose_common.projection.load_projection) and threads the JWT
    workspace claim, so a workspace-scoped role grant allows and everything
    else denies. Requires the live catalog key (perm:catalog:actions) that the
    running rbac-service materializes; skips when absent."""
    _require(8281, "OPA")
    _require(6379, "Redis")
    import json

    from windrose_common.redisx import build_redis

    from app.api.auth import OpaAuthzClient, Principal

    unique = uuid.uuid4().hex[:8]
    redis = build_redis()
    if not await redis.get("perm:catalog:actions"):
        await redis.aclose()
        pytest.skip("perm:catalog:actions not materialized (rbac-service not seeded)")
    tenant = f"tenant-{unique}"
    ws = f"ws-{unique}"
    # Workspace-scoped grant exactly as rbac's projector writes it.
    await redis.set(
        f"perm:{tenant}:u1:ws:{ws}",
        json.dumps({"actions": ["dataset.dataset.read"], "archived": False}),
    )
    client = OpaAuthzClient()
    principal = Principal(sub="u1", tenant_id=tenant, typ="user", scopes=[],
                          workspace_id=ws)
    try:
        assert await client.allow(principal, "dataset.dataset.read", None) is True
        # a verb the workspace grant does not carry is denied
        assert await client.allow(principal, "dataset.dataset.delete", None) is False
        # a different user with no projection entries is denied
        nobody = Principal(sub="u2", tenant_id=tenant, typ="user", scopes=[],
                           workspace_id=ws)
        assert await client.allow(nobody, "dataset.dataset.read", None) is False
    finally:
        await redis.delete(f"perm:{tenant}:u1:ws:{ws}")
        await redis.aclose()
