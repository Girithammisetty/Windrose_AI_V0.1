"""Integration: the service's REAL runtime adapters against live dev infra
(MinIO, Iceberg REST catalog, Vault). Proves the stub-removal wiring speaks the
real wire protocol. Auto-skips when an endpoint is unreachable."""

from __future__ import annotations

import hashlib
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.objectstore import S3ObjectStore
from app.domain.secrets import VaultSecretsStore
from app.domain.tablewriter import IcebergTableWriter, RowBatch

pytestmark = pytest.mark.integration


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _require(port: int, name: str) -> None:
    if not _reachable("localhost", port):
        pytest.skip(f"{name} not reachable on localhost:{port} — dev infra down")


async def _agen(chunks):
    for c in chunks:
        yield c


async def test_s3_object_store_streams_to_minio(tmp_path):
    _require(9000, "MinIO")
    store = S3ObjectStore("windrose-uploads")
    prefix = f"ing-it/{uuid.uuid4().hex[:8]}"
    key = f"{prefix}/part-1"
    data = b"col_a,col_b\n" + b"x,y\n" * 100_000  # ~400 KiB
    result = await store.put(key, _agen([data[i : i + 65536] for i in range(0, len(data), 65536)]))
    assert result.size == len(data)
    assert result.etag == hashlib.sha256(data).hexdigest()
    assert await store.exists(key) is True

    read = b""
    async for chunk in store.open_stream(key):
        read += chunk
    assert read == data
    assert await store.delete_prefix(prefix) >= 1


async def test_iceberg_writer_appends_and_reads_back():
    _require(8181, "Iceberg REST")
    _require(9000, "MinIO")
    from windrose_common.iceberg import IcebergRestCatalog

    table = f"bronze.ingit{uuid.uuid4().hex[:8]}.ds_orders"
    writer = IcebergTableWriter()
    catalog = IcebergRestCatalog()
    ing_id = "ing-real-1"

    async def batches() -> AsyncIterator[RowBatch]:
        yield RowBatch(columns=["id", "name"], rows=[["1", "a"], ["2", "b"]])

    try:
        staged = await writer.stage(table, batches(), {"ingestion_id": ing_id, "source": "upload"})
        result = await writer.commit(staged)
        assert result.rows_appended == 2
        assert result.snapshot_id > 0
        assert await writer.has_snapshot(table, ing_id) is True

        df = await catalog.read_snapshot(table, result.snapshot_id)
        assert len(df) == 2
        assert set(df.columns) == {"id", "name"}
    finally:
        await catalog.drop_table(table)


async def test_vault_secrets_roundtrip_and_grace_destroy():
    _require(8200, "Vault")
    store = VaultSecretsStore()
    tenant = uuid.uuid4().hex[:8]
    path = f"secret/data/tenants/{tenant}/connections/c1"
    await store.put(path, {"password": "p@ss", "username": "u"})
    assert await store.get(path) == {"password": "p@ss", "username": "u"}

    await store.schedule_destroy(path, datetime.now(UTC) - timedelta(seconds=1))
    assert await store.run_due_destroys() >= 1
    assert await store.get(path) is None
