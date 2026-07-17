"""Real Postgres driver (asyncpg) — probe, preview, streaming query pull.

Local-protocol driver verified against a dockerized Postgres. The watermark is
bound as a positional driver parameter (``$1``); its value is never spliced into
SQL text (ING-FR-061, BR-5).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import asyncpg
from pydantic import BaseModel

from app.domain.drivers.sql import to_positional
from app.domain.errors import ErrorCategory, TransientSourceError
from app.domain.probers import PreviewResult, ProbeResult

_AUTH_SQLSTATES = frozenset({"28P01", "28000"})


def _connect_kwargs(config: BaseModel, secrets: dict[str, str]) -> dict[str, Any]:
    ssl_mode = getattr(config, "ssl_mode", "prefer")
    # asyncpg accepts libpq sslmode strings; 'disable' -> no TLS (dockerized PG).
    ssl: Any = False if ssl_mode in ("disable", None) else ssl_mode
    return {
        "host": config.host,
        "port": getattr(config, "port", 5432),
        "user": config.username,
        "password": secrets.get("password"),
        "database": config.database,
        "ssl": ssl,
    }


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, asyncpg.PostgresError):
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate in _AUTH_SQLSTATES or isinstance(
            exc,
            asyncpg.InvalidPasswordError | asyncpg.InvalidAuthorizationSpecificationError,
        ):
            return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
        return ErrorCategory.SOURCE_UNREACHABLE, f"postgres error {sqlstate or 'unknown'}"
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return ErrorCategory.TIMEOUT, "connect timed out"
    return ErrorCategory.SOURCE_UNREACHABLE, "connect failed (scrubbed)"


class PostgresProber:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        try:
            conn = await asyncpg.connect(
                **_connect_kwargs(config, secrets), timeout=self.connect_timeout_s
            )
            try:
                await conn.fetchval("SELECT 1")
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


class PostgresPreviewer:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        target = request.get("query") or (
            f"SELECT * FROM {request['table']}" if request.get("table") else None
        )
        if not target:
            raise ValueError("preview requires table or query")
        conn = await asyncpg.connect(
            **_connect_kwargs(config, secrets), timeout=self.connect_timeout_s
        )
        preview_sql = f"SELECT * FROM ({target.rstrip(';')}) _p LIMIT {int(limit)}"
        try:
            records = await conn.fetch(preview_sql)
        finally:
            await conn.close()
        columns = list(records[0].keys()) if records else []
        rows = [dict(r) for r in records]
        return PreviewResult(columns=columns, rows=rows)


class PostgresQuerySource:
    def __init__(self, *, connect_timeout_s: float = 15.0, query_timeout_s: float = 1600.0) -> None:
        self.connect_timeout_s = connect_timeout_s
        self.query_timeout_s = query_timeout_s

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        conn = await asyncpg.connect(
            **_connect_kwargs(config, secrets), timeout=self.connect_timeout_s
        )
        try:
            # prepare plans the statement without executing it — cheap schema read.
            stmt = await conn.prepare(statement.strip().rstrip(";"))
            return [attr.name for attr in stmt.get_attributes()]
        except asyncpg.PostgresError as exc:
            raise TransientSourceError(
                ErrorCategory.SOURCE_UNREACHABLE, f"column introspection failed: {exc.sqlstate}"
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
        pg_sql, args = to_positional(sql, params)
        try:
            conn = await asyncpg.connect(
                **_connect_kwargs(config, secrets),
                timeout=self.connect_timeout_s,
                command_timeout=self.query_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            category, _ = _classify(exc)
            raise TransientSourceError(category, "connect failed (scrubbed)") from exc
        try:
            # Server-side cursor inside a transaction — bounded batches, never
            # materializing the full result (ING-FR-023).
            async with conn.transaction():
                cursor = await conn.cursor(pg_sql, *args)
                while True:
                    records = await cursor.fetch(batch_size)
                    if not records:
                        break
                    yield [dict(r) for r in records]
        finally:
            await conn.close()
