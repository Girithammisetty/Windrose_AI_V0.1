"""Connection probing + source preview ports (ING-FR-004/005).

Per-connector-type prober interface. FakeConnectionProber drives dev/tests
deterministically from the config's host/url/account marker strings; the real
driver-backed probers are stubs (TODO wave-2).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from app.domain.errors import ErrorCategory, UnsupportedConnectorError


@dataclass(slots=True)
class ProbeResult:
    status: str  # "ok" | "failed"
    latency_ms: int
    error_category: str | None = None
    error_detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(slots=True)
class PreviewResult:
    columns: list[str]
    rows: list[dict[str, Any]]


class ConnectionProber(Protocol):
    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult: ...


class SourcePreviewer(Protocol):
    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult: ...


class ProberRegistry:
    def __init__(self, default: ConnectionProber | None = None) -> None:
        self._default = default
        self._by_type: dict[str, ConnectionProber] = {}

    def set(self, connector_type: str, prober: ConnectionProber) -> None:
        self._by_type[connector_type] = prober

    def get(self, connector_type: str) -> ConnectionProber:
        prober = self._by_type.get(connector_type, self._default)
        if prober is None:
            raise NotImplementedError(f"no prober registered for {connector_type}")
        return prober


class UnsupportedConnectorProber:
    """Real-runtime registry default: any connector type without an explicitly
    wired driver FAILS with an honest UNSUPPORTED_CONNECTOR error instead of
    faking a successful probe (CONVENTIONS.md END STATE — no silent stubs)."""

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        raise UnsupportedConnectorError(getattr(config, "connector_type", "unknown"))


class UnsupportedSourcePreviewer:
    """Real-runtime previewer default: honest UNSUPPORTED_CONNECTOR instead of
    canned preview rows."""

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        raise UnsupportedConnectorError(getattr(config, "connector_type", "unknown"))


def _endpoint_marker(config: BaseModel) -> str:
    for attr in ("host", "url", "account", "bucket", "account_name", "project_id"):
        value = getattr(config, attr, None)
        if value:
            return str(value)
    return ""


class FakeConnectionProber:
    """Deterministic fake: outcome derives from the endpoint string.

    hosts containing 'unreachable' -> SOURCE_UNREACHABLE, 'badauth' ->
    AUTH_FAILED, 'slow' -> TIMEOUT; everything else probes ok.
    """

    def __init__(self, latency_ms: int = 5, delay_s: float = 0.0) -> None:
        self.latency_ms = latency_ms
        self.delay_s = delay_s  # real awaitable delay, to exercise the 15s timeout
        self.calls: list[str] = []

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        marker = _endpoint_marker(config)
        self.calls.append(marker)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        elapsed = max(self.latency_ms, int((time.monotonic() - started) * 1000))
        if "unreachable" in marker:
            return ProbeResult(
                "failed",
                elapsed,
                error_category=ErrorCategory.SOURCE_UNREACHABLE,
                error_detail="connect timeout (scrubbed)",
            )
        if "badauth" in marker:
            return ProbeResult(
                "failed",
                elapsed,
                error_category=ErrorCategory.AUTH_FAILED,
                error_detail="authentication failed (scrubbed)",
            )
        if "slow" in marker:
            return ProbeResult(
                "failed",
                elapsed,
                error_category=ErrorCategory.TIMEOUT,
                error_detail="probe timed out",
            )
        return ProbeResult("ok", elapsed)


@dataclass(slots=True)
class FakeSourcePreviewer:
    """Canned preview rows for dev/tests (ING-FR-005; never persists data)."""

    columns: list[str] = field(default_factory=lambda: ["id", "name", "updated_at"])
    rows: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": 1, "name": "alpha", "updated_at": "2026-07-01T00:00:00Z"},
            {"id": 2, "name": "beta", "updated_at": "2026-07-02T00:00:00Z"},
            {"id": 3, "name": "gamma", "updated_at": "2026-07-03T00:00:00Z"},
        ]
    )
    delay_s: float = 0.0  # real awaitable delay, to exercise the 30s preview timeout

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return PreviewResult(columns=list(self.columns), rows=self.rows[:limit])
