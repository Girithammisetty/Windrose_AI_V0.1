"""Real MinIO integration: multipart streaming put, read-back, move, delete,
delete_prefix, and presigned GET."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from windrose_common.objectstore import (
    S3BlobObjectStore,
    S3Config,
    S3StreamingObjectStore,
)

pytestmark = pytest.mark.integration


async def _agen(chunks):
    for c in chunks:
        yield c


async def test_streaming_multipart_put_get_roundtrip(minio, unique):
    cfg = S3Config.for_minio("windrose-uploads")
    store = S3StreamingObjectStore(cfg, part_size=5 * 1024 * 1024)  # 5 MiB parts
    key = f"pycommon-test/{unique}/blob.bin"

    # 6 MiB across two multipart parts (5 MiB + 1 MiB)
    payload = b"windrose-" * (700_000)  # ~6.3 MiB
    expected_sha = hashlib.sha256(payload).hexdigest()
    # feed in 512 KiB chunks to exercise the async-iterator buffering
    chunks = [payload[i : i + 512 * 1024] for i in range(0, len(payload), 512 * 1024)]

    result = await store.put(key, _agen(chunks))
    assert result.size == len(payload)
    assert result.etag == expected_sha  # sha256, not the S3 ETag
    assert await store.exists(key) is True
    assert await store.size(key) == len(payload)

    read = b""
    async for chunk in store.open_stream(key, chunk_size=256 * 1024):
        read += chunk
    assert hashlib.sha256(read).hexdigest() == expected_sha

    # move then delete_prefix cleanup
    moved = f"pycommon-test/{unique}/moved.bin"
    await store.move(key, moved)
    assert await store.exists(key) is False
    assert await store.exists(moved) is True

    removed = await store.delete_prefix(f"pycommon-test/{unique}")
    assert removed >= 1
    assert await store.exists(moved) is False


async def test_blob_put_get_and_presigned_url(minio, unique):
    cfg = S3Config.for_minio("windrose-profiles")
    store = S3BlobObjectStore(cfg)
    key = f"pycommon-test/{unique}/profile.json"
    body = b'{"rows": 42, "columns": 3}'

    await store.put(key, body, "application/json")
    assert await store.exists(key) is True
    assert await store.get(key) == body

    url = await store.signed_url(key, ttl_hours=1)
    assert "X-Amz-Signature" in url or "AWSAccessKeyId" in url
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    assert resp.status_code == 200
    assert resp.content == body

    await store.delete(key)
    assert await store.exists(key) is False
