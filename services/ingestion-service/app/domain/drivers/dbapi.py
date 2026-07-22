"""Generic DB-API 2.0 driver harness for the credential-gated warehouses.

Snowflake, Amazon Redshift and Databricks all ship synchronous PEP-249 drivers
with the same shape: connect → cursor → execute(sql, params) → fetchmany. This
module implements the prober / previewer / streaming query-source once against
that contract; each warehouse contributes a small :class:`DbapiDialect` (its
lazy-imported connect function, parameter translation and column-introspection
wrap).

These adapters are REAL — they drive the vendor SDK — but live end-to-end
verification needs real account credentials (CONVENTIONS.md "one honest
ceiling"). A contract test injects a fake DB-API connection to exercise the
adapter's SQL/param shaping and batch fetching without a live warehouse. The
``connect`` callable is injectable precisely so that test can substitute the
transport; the runtime default lazily imports and drives the real SDK.

The watermark rides out-of-band as a bound driver parameter — every dialect's
``translate`` emits a placeholder and carries the typed value separately, never
splicing it into SQL text (ING-FR-061, BR-5). Rows stream in bounded
``fetchmany`` batches (ING-FR-023); the sync driver runs in a worker thread so
the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.domain.drivers.sql import quote_identifier, wrap_limit_zero
from app.domain.errors import ErrorCategory, TransientSourceError
from app.domain.probers import PreviewResult, ProbeResult

# connect(config, secrets, timeout_s) -> a synchronous PEP-249 connection.
ConnectFn = Callable[[BaseModel, dict[str, str], float], Any]
TranslateFn = Callable[[str, dict[str, Any]], tuple[str, Any]]

_AUTH_MARKERS = ("password", "authent", "denied", "invalid credential", "login", "not authorized")


def default_classify(exc: Exception) -> tuple[str, str]:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT, "connect timed out"
    if any(marker in text for marker in _AUTH_MARKERS):
        return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
    return ErrorCategory.SOURCE_UNREACHABLE, "connect failed (scrubbed)"


@dataclass(slots=True)
class DbapiDialect:
    name: str
    connect: ConnectFn
    translate: TranslateFn
    columns_wrap: Callable[[str], str] = wrap_limit_zero
    probe_sql: str = "SELECT 1"
    classify: Callable[[Exception], tuple[str, str]] = default_classify


def _rows_as_dicts(cursor, rows: list[Any]) -> list[dict[str, Any]]:
    columns = [d[0] for d in cursor.description] if cursor.description else []
    out: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(dict(r))
        else:
            out.append(dict(zip(columns, r, strict=False)))
    return out


class DbapiProber:
    def __init__(self, dialect: DbapiDialect, *, connect_timeout_s: float = 15.0) -> None:
        self.d = dialect
        self.connect_timeout_s = connect_timeout_s

    def _probe_sync(self, config: BaseModel, secrets: dict[str, str]) -> None:
        conn = self.d.connect(config, secrets, self.connect_timeout_s)
        try:
            cur = conn.cursor()
            cur.execute(self.d.probe_sql)
            cur.fetchone()
        finally:
            conn.close()

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        try:
            await asyncio.to_thread(self._probe_sync, config, secrets)
        except Exception as exc:  # noqa: BLE001 — mapped to a scrubbed category
            category, detail = self.d.classify(exc)
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=category,
                error_detail=detail,
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class DbapiPreviewer:
    def __init__(self, dialect: DbapiDialect, *, connect_timeout_s: float = 15.0) -> None:
        self.d = dialect
        self.connect_timeout_s = connect_timeout_s

    def _preview_sync(
        self, config: BaseModel, secrets: dict[str, str], target: str, limit: int
    ) -> PreviewResult:
        conn = self.d.connect(config, secrets, self.connect_timeout_s)
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM ({target.rstrip(';')}) _p LIMIT {int(limit)}")
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
            return PreviewResult(columns=columns, rows=_rows_as_dicts(cur, list(rows)))
        finally:
            conn.close()

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        target = request.get("query") or (
            f"SELECT * FROM {quote_identifier(request['table'], quote='\"')}"
            if request.get("table") else None
        )
        if not target:
            raise ValueError("preview requires table or query")
        return await asyncio.to_thread(self._preview_sync, config, secrets, target, limit)


class DbapiQuerySource:
    def __init__(
        self,
        dialect: DbapiDialect,
        *,
        connect_timeout_s: float = 15.0,
        query_timeout_s: float = 1600.0,
    ) -> None:
        self.d = dialect
        self.connect_timeout_s = connect_timeout_s
        self.query_timeout_s = query_timeout_s

    def _columns_sync(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        conn = self.d.connect(config, secrets, self.connect_timeout_s)
        try:
            cur = conn.cursor()
            cur.execute(self.d.columns_wrap(statement))
            cur.fetchall()
            return [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        try:
            return await asyncio.to_thread(self._columns_sync, config, secrets, statement)
        except Exception as exc:  # noqa: BLE001
            category, detail = self.d.classify(exc)
            raise TransientSourceError(category, f"column introspection failed: {detail}") from exc

    async def execute(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        sql: str,
        params: dict[str, Any],
        batch_size: int,
    ):
        translated, bound = self.d.translate(sql, params)
        try:
            conn = await asyncio.to_thread(self.d.connect, config, secrets, self.query_timeout_s)
        except Exception as exc:  # noqa: BLE001
            category, detail = self.d.classify(exc)
            raise TransientSourceError(category, detail) from exc
        try:
            cur = conn.cursor()
            # `bound` rides out-of-band; `translated` holds only a placeholder.
            await asyncio.to_thread(cur.execute, translated, bound or None)
            while True:
                rows = await asyncio.to_thread(cur.fetchmany, batch_size)
                if not rows:
                    break
                yield _rows_as_dicts(cur, list(rows))
        finally:
            await asyncio.to_thread(conn.close)
