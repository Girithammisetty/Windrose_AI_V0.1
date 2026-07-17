"""ObjectStore implementations: local filesystem (dev/tests) + cloud stub."""

from __future__ import annotations

import hmac
import time
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote


class LocalFSObjectStore:
    """Local-filesystem object store. Signed URLs are HMAC-signed pseudo-URLs so
    tests can assert shape + expiry; production uses S3/GCS/Azure presigning."""

    def __init__(self, base_dir: str, signing_secret: str = "dev-signing-secret"):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self._secret = signing_secret.encode()

    def _path(self, key: str) -> Path:
        path = (self.base / key.lstrip("/")).resolve()
        if not path.is_relative_to(self.base.resolve()):
            raise ValueError(f"key escapes object store root: {key}")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    async def signed_url(self, key: str, ttl_hours: int) -> str:
        expires = int(time.time()) + ttl_hours * 3600
        sig = hmac.new(self._secret, f"{key}:{expires}".encode(), sha256).hexdigest()[:32]
        return f"https://objects.windrose.local/{quote(key)}?expires={expires}&sig={sig}"


class S3ObjectStore:
    """Real S3 blob store (MinIO in dev) via the shared ``windrose_common``
    adapter: profile blobs land in object storage and reads return real presigned
    GET URLs (24h TTL, DST-FR-027). Runtime object store."""

    def __init__(
        self,
        bucket: str = "windrose-profiles",
        *,
        endpoint_url: str = "http://localhost:9000",
        access_key: str = "windrose",
        secret_key: str = "windrose_dev",
        region: str = "us-east-1",
    ):
        from windrose_common.objectstore import S3BlobObjectStore, S3Config

        self._store = S3BlobObjectStore(
            S3Config(
                endpoint_url=endpoint_url,
                access_key=access_key,
                secret_key=secret_key,
                bucket=bucket,
                region=region,
            )
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self._store.put(key, data, content_type)

    async def get(self, key: str) -> bytes:
        return await self._store.get(key)

    async def exists(self, key: str) -> bool:
        return await self._store.exists(key)

    async def delete(self, key: str) -> None:
        await self._store.delete(key)

    async def signed_url(self, key: str, ttl_hours: int) -> str:
        return await self._store.signed_url(key, ttl_hours)
