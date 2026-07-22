"""Real Oracle driver (python-oracledb, THIN mode) — probe, preview, streaming pull.

Local-protocol driver verified against a dockerized Oracle
(``gvenzl/oracle-free``). THIN mode needs no Oracle Instant Client install.
python-oracledb exposes a native asyncio API (``connect_async``), so this driver
is genuinely async end-to-end.

Oracle's native bind style is ``:name`` — identical to the service-internal
``:watermark`` placeholder — so the watermark value is bound directly as a typed
driver parameter with no translation and no string splicing (ING-FR-061, BR-5).
Rows stream in bounded ``fetchmany`` batches (ING-FR-023).
"""

from __future__ import annotations

import time
from typing import Any

import oracledb
from pydantic import BaseModel

from app.domain.drivers.sql import quote_identifier, wrap_where_false
from app.domain.errors import ErrorCategory, TransientSourceError
from app.domain.probers import PreviewResult, ProbeResult

# ORA numbers meaning bad credentials / account issues.
_AUTH_ORA = frozenset({1017, 1005, 28000, 1031})


def _dsn(config: BaseModel) -> str:
    host = config.host
    port = getattr(config, "port", 1521)
    service = getattr(config, "service_name", None)
    return f"{host}:{port}/{service}"


def _connect_kwargs(
    config: BaseModel, secrets: dict[str, str], *, timeout: float
) -> dict[str, Any]:
    return {
        "user": config.username,
        "password": secrets.get("password") or "",
        "dsn": _dsn(config),
        "tcp_connect_timeout": timeout,
    }


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, oracledb.Error):
        err = exc.args[0] if exc.args else None
        code = getattr(err, "code", None)
        if code in _AUTH_ORA:
            return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
        return ErrorCategory.SOURCE_UNREACHABLE, f"oracle error ORA-{code or 'unknown'}"
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT, "connect timed out"
    return ErrorCategory.SOURCE_UNREACHABLE, "connect failed (scrubbed)"


class OracleProber:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        try:
            conn = await oracledb.connect_async(
                **_connect_kwargs(config, secrets, timeout=self.connect_timeout_s)
            )
            try:
                with conn.cursor() as cur:
                    await cur.execute("SELECT 1 FROM dual")
                    await cur.fetchone()
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001 — mapped to a scrubbed category
            category, detail = _classify(exc)
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=category,
                error_detail=detail,
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class OraclePreviewer:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        target = request.get("query") or (
            f"SELECT * FROM {quote_identifier(request['table'], quote='\"')}"
            if request.get("table") else None
        )
        if not target:
            raise ValueError("preview requires table or query")
        conn = await oracledb.connect_async(
            **_connect_kwargs(config, secrets, timeout=self.connect_timeout_s)
        )
        try:
            with conn.cursor() as cur:
                # Oracle has no LIMIT/TOP — FETCH FIRST n ROWS ONLY bounds the read.
                # Oracle rejects unquoted aliases starting with `_`.
                await cur.execute(
                    f"SELECT * FROM ({target.rstrip(';')}) wr_p FETCH FIRST {int(limit)} ROWS ONLY"
                )
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = await cur.fetchall()
        finally:
            await conn.close()
        return PreviewResult(
            columns=columns, rows=[dict(zip(columns, r, strict=False)) for r in rows]
        )


class OracleQuerySource:
    def __init__(self, *, connect_timeout_s: float = 15.0, query_timeout_s: float = 1600.0) -> None:
        self.connect_timeout_s = connect_timeout_s
        self.query_timeout_s = query_timeout_s

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        conn = await oracledb.connect_async(
            **_connect_kwargs(config, secrets, timeout=self.connect_timeout_s)
        )
        try:
            with conn.cursor() as cur:
                await cur.execute(wrap_where_false(statement))
                return [d[0] for d in cur.description] if cur.description else []
        except oracledb.Error as exc:
            raise TransientSourceError(
                ErrorCategory.SOURCE_UNREACHABLE, "column introspection failed"
            ) from exc
        finally:
            await conn.close()

    async def execute(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        sql: str,
        params: dict[str, Any],
        batch_size: int,
    ):
        try:
            conn = await oracledb.connect_async(
                **_connect_kwargs(config, secrets, timeout=self.connect_timeout_s)
            )
        except Exception as exc:  # noqa: BLE001
            category, _ = _classify(exc)
            raise TransientSourceError(category, "connect failed (scrubbed)") from exc
        try:
            with conn.cursor() as cur:
                cur.arraysize = batch_size
                # `:watermark` is Oracle's native bind — params carries the typed
                # value out-of-band; nothing is spliced into the SQL text.
                await cur.execute(sql.rstrip(";"), params or {})
                columns = [d[0] for d in cur.description] if cur.description else []
                while True:
                    rows = await cur.fetchmany(batch_size)
                    if not rows:
                        break
                    yield [dict(zip(columns, r, strict=False)) for r in rows]
        finally:
            await conn.close()
