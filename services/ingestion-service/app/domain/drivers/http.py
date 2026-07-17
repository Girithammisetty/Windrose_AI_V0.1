"""Real HTTP/cURL driver (httpx) — probe, preview, streaming fetch.

Honors the structured request spec (method, non-auth headers, auth from
secrets). Responses stream chunk-by-chunk into the object store, never buffered
whole (ING-FR-041). A lightweight SSRF guard rejects RFC1918 / link-local
targets per BR-6 (loopback is allowed for the local dev/test HTTP source).
"""

from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from app.domain.errors import ErrorCategory
from app.domain.objectstore import ObjectStore, PutResult
from app.domain.probers import PreviewResult, ProbeResult

_READ_CHUNK = 1024 * 1024  # 1 MiB
_MAX_BODY_BYTES = 1024 * 1024 * 1024  # BR-6: 1 GiB streamed cap
_REDIRECT_DEPTH = 3  # BR-6


class SsrfBlocked(Exception):
    """Target resolves to a disallowed (private / link-local) address."""


def _guard_host(host: str | None) -> None:
    """Reject a host that resolves to a link-local (cloud-metadata) or private,
    non-loopback address. Loopback stays allowed for local dev/test parity."""
    if not host:
        raise SsrfBlocked("missing host")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:  # DNS failure surfaces as SOURCE_UNREACHABLE upstream
        raise SsrfBlocked("dns resolution failed") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_link_local or (addr.is_private and not addr.is_loopback):
            raise SsrfBlocked(f"target address not allowed: {addr}")


def _guard_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfBlocked(f"scheme not allowed: {parsed.scheme!r}")
    _guard_host(parsed.hostname)


def _auth_and_headers(
    config: BaseModel, secrets: dict[str, str]
) -> tuple[dict[str, str], httpx.Auth | None]:
    headers = dict(getattr(config, "headers", {}) or {})
    auth: httpx.Auth | None = None
    if secrets.get("auth_header_value"):
        headers["Authorization"] = secrets["auth_header_value"]
    if secrets.get("basic_username") or secrets.get("basic_password"):
        auth = httpx.BasicAuth(secrets.get("basic_username", ""), secrets.get("basic_password", ""))
    return headers, auth


class HttpProber:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        url = config.url
        method = getattr(config, "method", "GET")
        headers, auth = _auth_and_headers(config, secrets)
        try:
            _guard_url(url)
            async with httpx.AsyncClient(
                timeout=self.connect_timeout_s, follow_redirects=True, max_redirects=_REDIRECT_DEPTH
            ) as client:
                # HEAD-or-GET trivial round-trip (ING-FR-004).
                probe_method = "HEAD" if method in ("GET", "HEAD") else method
                resp = await client.request(probe_method, url, headers=headers, auth=auth)
                if probe_method == "HEAD" and resp.status_code >= 400:
                    resp = await client.get(url, headers=headers, auth=auth)
            if resp.status_code == 401 or resp.status_code == 403:
                return ProbeResult(
                    "failed",
                    int((time.monotonic() - started) * 1000),
                    error_category=ErrorCategory.AUTH_FAILED,
                    error_detail=f"http {resp.status_code}",
                )
            if resp.status_code >= 400:
                return ProbeResult(
                    "failed",
                    int((time.monotonic() - started) * 1000),
                    error_category=ErrorCategory.SOURCE_UNREACHABLE,
                    error_detail=f"http {resp.status_code}",
                )
        except SsrfBlocked as exc:
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=ErrorCategory.SOURCE_UNREACHABLE,
                error_detail=str(exc),
            )
        except httpx.HTTPError:
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=ErrorCategory.SOURCE_UNREACHABLE,
                error_detail="request failed (scrubbed)",
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class HttpSourcePreviewer:
    def __init__(self, *, timeout_s: float = 30.0) -> None:
        self.timeout_s = timeout_s

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        url = request.get("path") or config.url
        method = getattr(config, "method", "GET")
        headers, auth = _auth_and_headers(config, secrets)
        _guard_url(url)
        lines: list[str] = []
        async with httpx.AsyncClient(
            timeout=self.timeout_s, follow_redirects=True, max_redirects=_REDIRECT_DEPTH
        ) as client:
            async with client.stream(method, url, headers=headers, auth=auth) as resp:
                resp.raise_for_status()
                buf = ""
                async for chunk in resp.aiter_text(_READ_CHUNK):
                    buf += chunk
                    while "\n" in buf and len(lines) < limit:
                        line, buf = buf.split("\n", 1)
                        lines.append(line)
                    if len(lines) >= limit:
                        break
                if buf and len(lines) < limit:
                    lines.append(buf)
        return PreviewResult(columns=["line"], rows=[{"line": ln} for ln in lines[:limit]])


class HttpSourceFetcher:
    def __init__(self, *, timeout_s: float = 300.0) -> None:
        self.timeout_s = timeout_s  # BR-6: per-run HTTP timeout 300s

    async def fetch(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        request: dict[str, Any],
        object_store: ObjectStore,
        dest_key: str,
    ) -> PutResult:
        url = request.get("path") or config.url
        method = getattr(config, "method", "GET")
        body = getattr(config, "body", None)
        headers, auth = _auth_and_headers(config, secrets)
        _guard_url(url)

        client = httpx.AsyncClient(
            timeout=self.timeout_s, follow_redirects=True, max_redirects=_REDIRECT_DEPTH
        )
        stream_cm = client.stream(
            method, url, headers=headers, auth=auth, content=body.encode() if body else None
        )
        resp = await stream_cm.__aenter__()

        async def stream():
            received = 0
            try:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(_READ_CHUNK):
                    received += len(chunk)
                    if received > _MAX_BODY_BYTES:
                        raise ValueError("response exceeded 1 GiB streamed cap (BR-6)")
                    yield chunk
            finally:
                await stream_cm.__aexit__(None, None, None)
                await client.aclose()

        return await object_store.put(dest_key, stream())
