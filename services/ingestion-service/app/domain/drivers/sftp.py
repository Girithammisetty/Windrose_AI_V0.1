"""Real SFTP driver (asyncssh) — probe (LIST), preview (LIST), streaming fetch.

Local-protocol driver verified against a dockerized SFTP server. File fetch is
memory-bounded: bytes are read in fixed chunks and streamed straight into the
object store — the whole file is never held in memory (ING-FR-041).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import asyncssh
from pydantic import BaseModel

from app.domain.errors import ErrorCategory
from app.domain.objectstore import ObjectStore, PutResult
from app.domain.probers import PreviewResult, ProbeResult

_READ_CHUNK = 1024 * 1024  # 1 MiB


def _connect_kwargs(config: BaseModel, secrets: dict[str, str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": config.host,
        "port": getattr(config, "port", 22),
        "username": config.username,
        "known_hosts": None,  # dev/test: trust-on-first-use; prod pins host keys
    }
    if secrets.get("private_key"):
        kwargs["client_keys"] = [asyncssh.import_private_key(secrets["private_key"])]
    if secrets.get("password"):
        kwargs["password"] = secrets["password"]
    return kwargs


@asynccontextmanager
async def _sftp(config: BaseModel, secrets: dict[str, str], connect_timeout_s: float):
    async with asyncssh.connect(
        **_connect_kwargs(config, secrets), connect_timeout=connect_timeout_s
    ) as conn:
        async with conn.start_sftp_client() as sftp:
            yield sftp


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, asyncssh.PermissionDenied):
        return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT, "connect timed out"
    return ErrorCategory.SOURCE_UNREACHABLE, "connect/list failed (scrubbed)"


class SftpProber:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        root = getattr(config, "root_directory", "/") or "/"
        try:
            async with _sftp(config, secrets, self.connect_timeout_s) as sftp:
                await sftp.listdir(root)  # trivial round-trip: SFTP LIST
        except Exception as exc:  # noqa: BLE001
            category, detail = _classify(exc)
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=category,
                error_detail=detail,
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class SftpSourcePreviewer:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        path = request.get("path") or getattr(config, "root_directory", "/") or "/"
        async with _sftp(config, secrets, self.connect_timeout_s) as sftp:
            names = sorted(n for n in await sftp.listdir(path) if n not in (".", ".."))
        rows = [{"name": n} for n in names[:limit]]
        return PreviewResult(columns=["name"], rows=rows)


class SftpSourceFetcher:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def fetch(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        request: dict[str, Any],
        object_store: ObjectStore,
        dest_key: str,
    ) -> PutResult:
        remote_path = request.get("path") or request.get("remote_path")
        if not remote_path:
            raise ValueError("sftp fetch requires a remote path")
        async with _sftp(config, secrets, self.connect_timeout_s) as sftp:
            handle = await sftp.open(remote_path, "rb")

            async def stream():
                try:
                    while True:
                        chunk = await handle.read(_READ_CHUNK)
                        if not chunk:
                            break
                        yield chunk if isinstance(chunk, bytes) else chunk.encode()
                finally:
                    await handle.close()

            return await object_store.put(dest_key, stream())
