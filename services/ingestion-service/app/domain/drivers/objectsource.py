"""Object-store / data-lake SOURCE connectors (ING-FR-002/004/005/041/064).

A cloud bucket (S3/GCS/Azure Blob) is a *file* source, not a SQL query source:
the engine lists objects under a prefix, filters by an optional ``glob`` and an
optional incremental **watermark** (object ``LastModified`` mtime), then streams
each matching object chunk-by-chunk through the shared format decoders into the
bronze table. Nothing ever buffers a whole object — the fetch/decode path is a
pure async byte→row-batch pipeline (memory bound, AC-4).

The three backends (S3/GCS/Azure) share this engine; each supplies a small
``ObjectStoreClient`` via a ``client_factory`` (exactly like the BigQuery
driver's injectable client). S3 is verified live against MinIO; GCS/Azure are
credential-gated (real SDK, contract-tested with an injected client).

Incremental watermark (ING-FR-061/BR-5): the "since" bound is ALWAYS a typed
``datetime`` compared directly against each object's typed ``LastModified``. It
never enters any request string — the list call carries only Bucket+Prefix, and
selection happens client-side on typed values. No literal splicing anywhere.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from typing import Any, Protocol

from pydantic import BaseModel

from app.domain.decode import DecodeOptions, DecodeStats, decode_stream
from app.domain.errors import ErrorCategory
from app.domain.objectstore import ObjectStore, PutResult
from app.domain.probers import PreviewResult, ProbeResult
from app.domain.watermark import coerce_watermark, serialize_watermark

_READ_CHUNK = 1024 * 1024  # 1 MiB streamed read chunks (memory bound)


@dataclass(slots=True)
class ObjectRef:
    """A listed object: key + size + typed last-modified time (mtime)."""

    key: str
    size: int
    last_modified: datetime | None


class ReadBody(Protocol):
    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class ObjectStoreClient(Protocol):
    """Per-backend sync client (wrapped in threads by the async engine)."""

    def probe(self) -> None: ...

    def list_objects(self, prefix: str) -> list[ObjectRef]: ...

    def open_read(self, key: str) -> ReadBody: ...

    def close(self) -> None: ...


ClientFactory = Callable[[BaseModel, dict[str, str], float], ObjectStoreClient]


# --------------------------------------------------------------------------- selection


def _norm_prefix(prefix: str | None) -> str:
    return (prefix or "").lstrip("/")


def match_glob(key: str, glob: str | None) -> bool:
    """Match either the full key or its basename against a shell glob."""
    if not glob:
        return True
    return fnmatch(key, glob) or fnmatch(key.rsplit("/", 1)[-1], glob)


def coerce_since(value: Any) -> datetime | None:
    """Coerce a persisted watermark into a typed ``datetime`` (never spliced)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return coerce_watermark("timestamp", value)


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def select_objects(
    refs: list[ObjectRef], *, glob: str | None = None, since: datetime | None = None
) -> list[ObjectRef]:
    """Filter listed objects by glob + incremental mtime, oldest-first.

    ``since`` is a typed ``datetime``; only objects strictly newer than it are
    kept (BR-5 typed comparison). Zero-byte "directory marker" keys are skipped.
    """
    since_aware = _as_aware(since) if since is not None else None
    out: list[ObjectRef] = []
    for ref in refs:
        if ref.key.endswith("/"):
            continue
        if not match_glob(ref.key, glob):
            continue
        if since_aware is not None:
            if ref.last_modified is None or not (_as_aware(ref.last_modified) > since_aware):
                continue
        out.append(ref)
    _epoch = datetime.min.replace(tzinfo=UTC)
    out.sort(key=lambda r: (_as_aware(r.last_modified) if r.last_modified else _epoch, r.key))
    return out


def newest_mtime(refs: list[ObjectRef]) -> datetime | None:
    times = [_as_aware(r.last_modified) for r in refs if r.last_modified is not None]
    return max(times) if times else None


def _classify(exc: Exception) -> tuple[str, str]:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT, "list timed out"
    if any(m in text for m in ("denied", "forbidden", "unauthor", "invalidaccesskey", "signature")):
        return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
    return ErrorCategory.SOURCE_UNREACHABLE, "bucket list failed (scrubbed)"


async def _stream_object(client: ObjectStoreClient, key: str) -> AsyncIterator[bytes]:
    """Stream one object chunk-by-chunk — never buffers the whole object."""
    import asyncio

    body = await asyncio.to_thread(client.open_read, key)
    try:
        while True:
            chunk = await asyncio.to_thread(body.read, _READ_CHUNK)
            if not chunk:
                break
            yield chunk if isinstance(chunk, bytes) else bytes(chunk)
    finally:
        await asyncio.to_thread(body.close)


def _selection(config: BaseModel, request: dict[str, Any]) -> tuple[str, str | None, str]:
    prefix = _norm_prefix(request.get("prefix") or getattr(config, "root_prefix", "/"))
    glob = request.get("glob") if request.get("glob") is not None else getattr(config, "glob", None)
    file_format = request.get("file_format") or getattr(config, "file_format", "csv")
    return prefix, glob, file_format


# --------------------------------------------------------------------------- probe / preview


class ObjectStoreProber:
    """ING-FR-004: test-connection = list the bucket (trivial round-trip)."""

    def __init__(self, factory: ClientFactory, *, connect_timeout_s: float = 15.0) -> None:
        self._factory = factory
        self.connect_timeout_s = connect_timeout_s

    def _probe_sync(self, config: BaseModel, secrets: dict[str, str]) -> None:
        client = self._factory(config, secrets, self.connect_timeout_s)
        try:
            client.probe()
        finally:
            _safe_close(client)

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        import asyncio

        started = time.monotonic()
        try:
            await asyncio.to_thread(self._probe_sync, config, secrets)
        except Exception as exc:  # noqa: BLE001
            category, detail = _classify(exc)
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=category,
                error_detail=detail,
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class ObjectStoreSourcePreviewer:
    """ING-FR-005: preview = first matching object's first rows (decoded)."""

    def __init__(self, factory: ClientFactory, *, connect_timeout_s: float = 30.0) -> None:
        self._factory = factory
        self.connect_timeout_s = connect_timeout_s

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        import asyncio

        prefix, glob, file_format = _selection(config, request)
        client = await asyncio.to_thread(self._factory, config, secrets, self.connect_timeout_s)
        try:
            refs = select_objects(
                await asyncio.to_thread(client.list_objects, prefix), glob=glob, since=None
            )
            if not refs:
                return PreviewResult(columns=[], rows=[])
            ref = refs[0]
            stats = DecodeStats()
            opts = DecodeOptions(file_format=file_format, batch_size=max(limit, 1))
            columns: list[str] = []
            rows: list[dict[str, Any]] = []
            async for batch in decode_stream(_stream_object(client, ref.key), opts, stats):
                columns = batch.columns
                for row in batch.rows:
                    rows.append(dict(zip(columns, row, strict=False)))
                    if len(rows) >= limit:
                        break
                if len(rows) >= limit:
                    break
            return PreviewResult(columns=columns, rows=rows[:limit])
        finally:
            await asyncio.to_thread(_safe_close, client)


# --------------------------------------------------------------------------- fetch


class ObjectStoreSourceFetcher:
    """Streaming single-object fetch into the object store (ING-FR-041) plus the
    listing primitives the incremental ingestor uses. ``fetch`` mirrors the
    SFTP/HTTP fetchers: one object → one dest key, streamed chunk-by-chunk."""

    def __init__(self, factory: ClientFactory, *, connect_timeout_s: float = 15.0) -> None:
        self._factory = factory
        self.connect_timeout_s = connect_timeout_s

    async def list_objects(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        *,
        since: datetime | None = None,
        prefix: str | None = None,
        glob: str | None = None,
    ) -> list[ObjectRef]:
        import asyncio

        eff_prefix = _norm_prefix(
            prefix if prefix is not None else getattr(config, "root_prefix", "/")
        )
        eff_glob = glob if glob is not None else getattr(config, "glob", None)
        client = await asyncio.to_thread(self._factory, config, secrets, self.connect_timeout_s)
        try:
            refs = await asyncio.to_thread(client.list_objects, eff_prefix)
        finally:
            await asyncio.to_thread(_safe_close, client)
        return select_objects(refs, glob=eff_glob, since=coerce_since(since))

    async def fetch(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        request: dict[str, Any],
        object_store: ObjectStore,
        dest_key: str,
    ) -> PutResult:
        import asyncio

        key = request.get("key") or request.get("path")
        if not key:
            raise ValueError("object-store fetch requires an object 'key'")
        client = await asyncio.to_thread(self._factory, config, secrets, self.connect_timeout_s)

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in _stream_object(client, key):
                    yield chunk
            finally:
                await asyncio.to_thread(_safe_close, client)

        return await object_store.put(dest_key, stream())


# --------------------------------------------------------------------------- ingestor


@dataclass(slots=True)
class IngestResult:
    rows: int
    objects: int
    bytes_written: int
    snapshot_id: int
    new_watermark: str | None  # serialized max mtime observed (next run's "since")


class ObjectSourceIngestor:
    """Full data-lake source pull: list → filter (glob + incremental mtime) →
    stream-decode every matching object → single Iceberg/bronze snapshot.

    This is the real runtime pipeline a file-poll schedule (ING-FR-064) drives;
    it reuses the shared decoders and the two-phase TableWriter (BR-9: exactly one
    snapshot). Memory-bounded: each object is streamed chunk-by-chunk through the
    decoder — the whole file is never held in memory.
    """

    def __init__(self, factory: ClientFactory, *, connect_timeout_s: float = 15.0) -> None:
        self._factory = factory
        self.connect_timeout_s = connect_timeout_s

    async def ingest(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        *,
        table_writer: Any,
        table: str,
        ingestion_id: str,
        since: datetime | None = None,
        batch_size: int = 5000,
        error_row_limit: int = 100,
    ) -> IngestResult:
        import asyncio

        connector_type = getattr(config, "connector_type", "object")
        prefix = _norm_prefix(getattr(config, "root_prefix", "/"))
        glob = getattr(config, "glob", None)
        file_format = getattr(config, "file_format", "csv")

        client = await asyncio.to_thread(self._factory, config, secrets, self.connect_timeout_s)
        try:
            all_refs = await asyncio.to_thread(client.list_objects, prefix)
            refs = select_objects(all_refs, glob=glob, since=coerce_since(since))
            stats = DecodeStats()
            opts = DecodeOptions(
                file_format=file_format, error_row_limit=error_row_limit, batch_size=batch_size
            )

            async def batches() -> AsyncIterator[Any]:
                for ref in refs:
                    async for batch in decode_stream(
                        _stream_object(client, ref.key), opts, stats
                    ):
                        yield batch

            staged = await table_writer.stage(
                table,
                batches(),
                {"ingestion_id": ingestion_id, "source": f"object:{connector_type}"},
            )
        finally:
            await asyncio.to_thread(_safe_close, client)

        result = await table_writer.commit(staged)
        return IngestResult(
            rows=result.rows_appended,
            objects=len(refs),
            bytes_written=result.bytes_written,
            snapshot_id=result.snapshot_id,
            new_watermark=serialize_watermark(newest_mtime(refs)) if refs else (
                serialize_watermark(since) if since else None
            ),
        )


def _safe_close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass
