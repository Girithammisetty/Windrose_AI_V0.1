"""Real FTP / FTPS driver (aioftp) — probe (LIST), preview (LIST), streaming fetch.

Local-protocol driver, mirroring the SFTP driver but over FTP: verified against a
real FTP server. File fetch is memory-bounded — bytes are read block-by-block from
the FTP data connection and streamed straight into the object store; the whole
file is never held in memory (ING-FR-041). FTPS (explicit TLS) is selected by the
``ftps`` config flag (ING-FR-008).
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

import aioftp
from pydantic import BaseModel

from app.domain.errors import ErrorCategory
from app.domain.objectstore import ObjectStore, PutResult
from app.domain.probers import PreviewResult, ProbeResult

_READ_BLOCK = 1024 * 1024  # 1 MiB


@asynccontextmanager
async def _ftp(config: BaseModel, secrets: dict[str, str], connect_timeout_s: float):
    client = aioftp.Client(ssl=True) if getattr(config, "ftps", False) else aioftp.Client()
    await asyncio.wait_for(
        client.connect(config.host, getattr(config, "port", 21)), timeout=connect_timeout_s
    )
    try:
        await asyncio.wait_for(
            client.login(config.username, secrets.get("password", "")),
            timeout=connect_timeout_s,
        )
        yield client
    finally:
        try:
            await client.quit()
        except Exception:  # noqa: BLE001
            client.close()


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return ErrorCategory.TIMEOUT, "connect timed out"
    if isinstance(exc, aioftp.StatusCodeError) and any(
        str(c).startswith("53") for c in getattr(exc, "received_codes", ())
    ):
        return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
    if "530" in str(exc):
        return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
    return ErrorCategory.SOURCE_UNREACHABLE, "connect/list failed (scrubbed)"


class FtpProber:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        root = getattr(config, "root_directory", "/") or "/"
        try:
            async with _ftp(config, secrets, self.connect_timeout_s) as client:
                async for _entry in client.list(root):  # trivial round-trip: FTP LIST
                    break
        except Exception as exc:  # noqa: BLE001
            category, detail = _classify(exc)
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=category,
                error_detail=detail,
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class FtpSourcePreviewer:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        path = request.get("path") or getattr(config, "root_directory", "/") or "/"
        names: list[str] = []
        async with _ftp(config, secrets, self.connect_timeout_s) as client:
            async for path_obj, _info in client.list(path):  # aioftp: (PurePosixPath, info)
                names.append(path_obj.name)
        names.sort()
        return PreviewResult(columns=["name"], rows=[{"name": n} for n in names[:limit]])


class FtpSourceFetcher:
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
        remote_path = request.get("path") or request.get("remote_path") or request.get("key")
        if not remote_path:
            raise ValueError("ftp fetch requires a remote path")

        client = aioftp.Client(ssl=True) if getattr(config, "ftps", False) else aioftp.Client()
        await asyncio.wait_for(
            client.connect(config.host, getattr(config, "port", 21)),
            timeout=self.connect_timeout_s,
        )
        await asyncio.wait_for(
            client.login(config.username, secrets.get("password", "")),
            timeout=self.connect_timeout_s,
        )

        async def stream():
            try:
                async with client.download_stream(remote_path) as ftp_stream:
                    async for block in ftp_stream.iter_by_block(_READ_BLOCK):
                        yield bytes(block)
            finally:
                try:
                    await client.quit()
                except Exception:  # noqa: BLE001
                    client.close()

        return await object_store.put(dest_key, stream())
