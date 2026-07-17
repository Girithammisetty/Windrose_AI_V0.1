"""Real MySQL driver (aiomysql) — probe, preview, streaming query pull.

Local-protocol driver verified against a dockerized MySQL. The watermark is
bound as a pyformat driver parameter (``%(watermark)s``) with its typed value
passed out-of-band; never spliced into SQL text (ING-FR-061, BR-5). Streaming
uses an unbuffered server-side cursor (``SSDictCursor``) so results are pulled
in bounded batches (ING-FR-023).
"""

from __future__ import annotations

import time
from typing import Any

import aiomysql
import pymysql
from pydantic import BaseModel

from app.domain.drivers.sql import to_pyformat, wrap_limit_zero
from app.domain.errors import ErrorCategory, TransientSourceError
from app.domain.probers import PreviewResult, ProbeResult

_ACCESS_DENIED = frozenset({1044, 1045, 1698})  # bad password / no db access


def _connect_kwargs(config: BaseModel, secrets: dict[str, str]) -> dict[str, Any]:
    return {
        "host": config.host,
        "port": getattr(config, "port", 3306),
        "user": config.username,
        "password": secrets.get("password") or "",
        "db": config.database,
    }


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, pymysql.err.OperationalError) and exc.args:
        code = exc.args[0]
        if code in _ACCESS_DENIED:
            return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
        return ErrorCategory.SOURCE_UNREACHABLE, f"mysql error {code}"
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT, "connect timed out"
    return ErrorCategory.SOURCE_UNREACHABLE, "connect failed (scrubbed)"


class MysqlProber:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        try:
            conn = await aiomysql.connect(
                **_connect_kwargs(config, secrets), connect_timeout=self.connect_timeout_s
            )
            try:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    await cur.fetchone()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — mapped to a scrubbed category
            category, detail = _classify(exc)
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=category,
                error_detail=detail,
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class MysqlPreviewer:
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
        conn = await aiomysql.connect(
            **_connect_kwargs(config, secrets), connect_timeout=self.connect_timeout_s
        )
        try:
            async with conn.cursor(aiomysql.cursors.DictCursor) as cur:
                await cur.execute(f"SELECT * FROM ({target.rstrip(';')}) _p LIMIT {int(limit)}")
                rows = list(await cur.fetchall())
                columns = [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()
        return PreviewResult(columns=columns, rows=[dict(r) for r in rows])


class MysqlQuerySource:
    def __init__(self, *, connect_timeout_s: float = 15.0, query_timeout_s: float = 1600.0) -> None:
        self.connect_timeout_s = connect_timeout_s
        self.query_timeout_s = query_timeout_s

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        conn = await aiomysql.connect(
            **_connect_kwargs(config, secrets), connect_timeout=self.connect_timeout_s
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(wrap_limit_zero(statement))
                return [d[0] for d in cur.description] if cur.description else []
        except pymysql.err.MySQLError as exc:
            raise TransientSourceError(
                ErrorCategory.SOURCE_UNREACHABLE, "column introspection failed"
            ) from exc
        finally:
            conn.close()

    async def execute(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        sql: str,
        params: dict[str, Any],
        batch_size: int,
    ):
        my_sql, my_params = to_pyformat(sql, params)
        try:
            conn = await aiomysql.connect(
                **_connect_kwargs(config, secrets), connect_timeout=self.connect_timeout_s
            )
        except Exception as exc:  # noqa: BLE001
            category, _ = _classify(exc)
            raise TransientSourceError(category, "connect failed (scrubbed)") from exc
        try:
            # SSDictCursor streams rows from the server without buffering the
            # whole result set (ING-FR-023).
            async with conn.cursor(aiomysql.cursors.SSDictCursor) as cur:
                await cur.execute(my_sql, my_params or None)
                while True:
                    rows = await cur.fetchmany(batch_size)
                    if not rows:
                        break
                    yield [dict(r) for r in rows]
        finally:
            conn.close()
