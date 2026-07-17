"""ObjectStore port (ING-FR-040/041/043).

All byte movement is streaming: `put` consumes an async byte iterator and
`open_stream` yields bounded chunks — the service never buffers a whole file.
LocalFSObjectStore backs the unit tier; S3ObjectStore is the real runtime store
(MinIO/S3 multipart via windrose_common).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.ids import uuid7

DEFAULT_CHUNK = 1024 * 1024  # 1 MiB read chunks


@dataclass(slots=True)
class PutResult:
    size: int
    etag: str  # sha256 hex of content


class ObjectStore(Protocol):
    async def put(self, key: str, stream: AsyncIterator[bytes]) -> PutResult: ...

    def open_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK) -> AsyncIterator[bytes]: ...

    async def exists(self, key: str) -> bool: ...

    async def size(self, key: str) -> int: ...

    async def move(self, src: str, dst: str) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> int: ...


class LocalFSObjectStore:
    """Filesystem-backed store for dev/tests. Writes stream chunk-by-chunk."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key.lstrip("/")).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"object key escapes store root: {key!r}")
        return path

    async def put(self, key: str, stream: AsyncIterator[bytes]) -> PutResult:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid7()}.tmp")
        digest = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as fh:
                async for chunk in stream:
                    fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return PutResult(size=size, etag=digest.hexdigest())

    async def open_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK) -> AsyncIterator[bytes]:
        path = self._path(key)
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    return
                yield chunk

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    async def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    async def move(self, src: str, dst: str) -> None:
        dst_path = self._path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self._path(src), dst_path)

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix)
        if not base.is_dir():
            return 0
        count = 0
        for path in sorted(base.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
                count += 1
            else:
                path.rmdir()
        base.rmdir()
        return count


class S3ObjectStore:
    """Real S3 object store (MinIO in dev, any S3 API in prod) via the shared
    ``windrose_common`` streaming adapter: multipart upload with at most one part
    buffered in memory (ING-FR-041). ``etag`` is the sha256 of the content, so the
    checksum semantics match ``LocalFSObjectStore``. Runtime object store."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str = "http://localhost:9000",
        access_key: str = "windrose",
        secret_key: str = "windrose_dev",
        region: str = "us-east-1",
        part_size: int = 8 * 1024 * 1024,
    ) -> None:
        from windrose_common.objectstore import S3Config, S3StreamingObjectStore

        cfg = S3Config(
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            region=region,
        )
        self._store = S3StreamingObjectStore(cfg, part_size=part_size)

    async def put(self, key: str, stream: AsyncIterator[bytes]) -> PutResult:
        result = await self._store.put(key, stream)
        return PutResult(size=result.size, etag=result.etag)

    def open_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK) -> AsyncIterator[bytes]:
        return self._store.open_stream(key, chunk_size)

    async def exists(self, key: str) -> bool:
        return await self._store.exists(key)

    async def size(self, key: str) -> int:
        return await self._store.size(key)

    async def move(self, src: str, dst: str) -> None:
        await self._store.move(src, dst)

    async def delete(self, key: str) -> None:
        await self._store.delete(key)

    async def delete_prefix(self, prefix: str) -> int:
        return await self._store.delete_prefix(prefix)
