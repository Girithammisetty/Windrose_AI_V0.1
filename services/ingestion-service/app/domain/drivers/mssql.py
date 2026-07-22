"""Real Microsoft SQL Server / Azure Synapse driver (pymssql).

Local-protocol driver verified against a dockerized SQL Server
(``mcr.microsoft.com/mssql/server:2022``). pymssql speaks the TDS wire protocol
directly — no ODBC system dependency. The same driver backs both ``sqlserver``
and ``synapse`` (Synapse exposes a T-SQL / TDS endpoint).

pymssql is a synchronous DB-API driver; every blocking call runs in a worker
thread via ``asyncio.to_thread`` so the event loop is never blocked and the
service's async contract holds. The watermark is bound as a pyformat driver
parameter (``%(watermark)s``) with its typed value passed out-of-band — never
spliced into SQL text (ING-FR-061, BR-5). Rows stream in bounded ``fetchmany``
batches (ING-FR-023).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pymssql
from pydantic import BaseModel

from app.domain.drivers.sql import quote_bracket_identifier, to_pyformat, wrap_top_zero
from app.domain.errors import ErrorCategory, TransientSourceError
from app.domain.probers import PreviewResult, ProbeResult

# SQL Server login failure numbers (bad password / no db access / cannot open db).
_AUTH_NUMBERS = frozenset({18456, 18452, 4060, 40615})


def _connect_kwargs(
    config: BaseModel, secrets: dict[str, str], *, timeout: float
) -> dict[str, Any]:
    return {
        "server": config.host,
        "port": str(getattr(config, "port", 1433)),
        "user": config.username,
        "password": secrets.get("password") or "",
        "database": getattr(config, "database", None),
        "login_timeout": int(timeout),
        "timeout": int(timeout),
        "as_dict": True,
    }


# Login-failure signatures FreeTDS/pymssql may surface in the DB-Lib message
# even when args[0] is NOT the clean SQL Server number. Depending on the TDS
# protocol version and driver build, a bad-password login to a *reachable*
# server can arrive as an OperationalError whose args[0] is the DB-Lib message
# (not int 18456), leaving the SQL number only inside the text — so we scan the
# text for these too. None of these appear for a genuinely unreachable/timed-out
# server ("unable to connect", "server is unavailable or does not exist"), so
# this cannot misclassify a network failure as auth (no false positives).
_AUTH_TEXTS = (
    "login failed", "login incorrect", "cannot open database", "password",
    "18456", "18452", "4060", "40615",
)


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, pymssql.OperationalError):
        # args[0] is the SQL Server error number only when it is actually an int;
        # FreeTDS sometimes puts the message string there instead.
        number = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        text = str(exc).lower()
        if number in _AUTH_NUMBERS or any(sig in text for sig in _AUTH_TEXTS):
            return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
        return ErrorCategory.SOURCE_UNREACHABLE, f"sqlserver error {number or 'unknown'}"
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT, "connect timed out"
    return ErrorCategory.SOURCE_UNREACHABLE, "connect failed (scrubbed)"


class SqlServerProber:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    def _probe_sync(self, config: BaseModel, secrets: dict[str, str]) -> None:
        conn = pymssql.connect(**_connect_kwargs(config, secrets, timeout=self.connect_timeout_s))
        try:
            cur = conn.cursor()
            # Name the column — with as_dict=True pymssql cannot key an anonymous
            # column (`SELECT 1`) into a dict.
            cur.execute("SELECT 1 AS probe")
            cur.fetchone()
        finally:
            conn.close()

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        try:
            await asyncio.to_thread(self._probe_sync, config, secrets)
        except Exception as exc:  # noqa: BLE001 — mapped to a scrubbed category
            category, detail = _classify(exc)
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=category,
                error_detail=detail,
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class SqlServerPreviewer:
    def __init__(self, *, connect_timeout_s: float = 15.0) -> None:
        self.connect_timeout_s = connect_timeout_s

    def _preview_sync(
        self, config: BaseModel, secrets: dict[str, str], target: str, limit: int
    ) -> PreviewResult:
        conn = pymssql.connect(**_connect_kwargs(config, secrets, timeout=self.connect_timeout_s))
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT TOP {int(limit)} * FROM ({target.rstrip(';')}) _p")
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()
        return PreviewResult(columns=columns, rows=[dict(r) for r in rows])

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        target = request.get("query") or (
            f"SELECT * FROM {quote_bracket_identifier(request['table'])}"
            if request.get("table") else None
        )
        if not target:
            raise ValueError("preview requires table or query")
        return await asyncio.to_thread(self._preview_sync, config, secrets, target, limit)


class SqlServerQuerySource:
    def __init__(self, *, connect_timeout_s: float = 15.0, query_timeout_s: float = 1600.0) -> None:
        self.connect_timeout_s = connect_timeout_s
        self.query_timeout_s = query_timeout_s

    def _columns_sync(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        conn = pymssql.connect(**_connect_kwargs(config, secrets, timeout=self.connect_timeout_s))
        try:
            cur = conn.cursor()
            cur.execute(wrap_top_zero(statement))
            cur.fetchall()
            return [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        try:
            return await asyncio.to_thread(self._columns_sync, config, secrets, statement)
        except pymssql.Error as exc:
            raise TransientSourceError(
                ErrorCategory.SOURCE_UNREACHABLE, "column introspection failed"
            ) from exc

    async def execute(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        sql: str,
        params: dict[str, Any],
        batch_size: int,
    ):
        ms_sql, ms_params = to_pyformat(sql, params)
        try:
            conn = await asyncio.to_thread(
                lambda: pymssql.connect(
                    **_connect_kwargs(config, secrets, timeout=self.query_timeout_s)
                )
            )
        except Exception as exc:  # noqa: BLE001
            category, _ = _classify(exc)
            raise TransientSourceError(category, "connect failed (scrubbed)") from exc
        try:
            cur = conn.cursor()
            # pymssql performs the parameter substitution itself with proper
            # quoting; ms_params rides out-of-band (never spliced by us).
            await asyncio.to_thread(cur.execute, ms_sql, ms_params or None)
            while True:
                rows = await asyncio.to_thread(cur.fetchmany, batch_size)
                if not rows:
                    break
                yield [dict(r) for r in rows]
        finally:
            await asyncio.to_thread(conn.close)
