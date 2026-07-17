"""Query source port (ING-FR-023, ING-FR-061).

Streams query results in bounded batches; never materializes the full result.
FakeQuerySource backs dev/tests — it records every (sql, params) call so tests
can assert driver-level parameter binding (AC-8), honours watermark params by
filtering its canned rows, and can simulate transient outages (AC-12).
Real drivers are stubs (TODO wave-2).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.domain.errors import ErrorCategory, PermanentJobError, TransientSourceError

_WATERMARK_RE = re.compile(r"WHERE\s+([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|>|<|=)\s*:watermark")


class UnsupportedQuerySource:
    """Real-runtime registry default: a query job against a connector type with
    no wired driver fails PERMANENTLY with a categorized, honest error instead
    of "ingesting" canned fake rows (or zero rows) silently."""

    @staticmethod
    def _raise(config: BaseModel) -> None:
        ctype = getattr(config, "connector_type", "unknown")
        raise PermanentJobError(
            ErrorCategory.INTERNAL,
            f"UNSUPPORTED_CONNECTOR: no query driver wired for connector type "
            f"{ctype!r} in this deployment",
            hint="use a supported connector type or deploy the missing driver",
        )

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        self._raise(config)
        return []  # pragma: no cover - unreachable

    async def execute(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        sql: str,
        params: dict[str, Any],
        batch_size: int,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        self._raise(config)
        yield []  # pragma: no cover - unreachable


class QuerySourceRegistry:
    def __init__(self, default: Any | None = None) -> None:
        self._default = default
        self._by_type: dict[str, Any] = {}

    def set(self, connector_type: str, source: Any) -> None:
        self._by_type[connector_type] = source

    def get(self, connector_type: str) -> Any:
        source = self._by_type.get(connector_type, self._default)
        if source is None:
            raise NotImplementedError(f"no query source registered for {connector_type}")
        return source


def _coerce_like(param: Any, raw: Any) -> Any:
    """Coerce a raw row value to the type of the bound watermark parameter."""
    if isinstance(param, datetime):
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if isinstance(param, date):
        return date.fromisoformat(str(raw))
    if isinstance(param, Decimal):
        return Decimal(str(raw))
    if isinstance(param, int):
        return int(raw)
    return str(raw)


class FakeQuerySource:
    """In-memory rows; behaves like a driver with bound parameters."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        fail_attempts: int = 0,
        delay_s: float = 0.0,
    ) -> None:
        self.rows: list[dict[str, Any]] = rows or []
        self.fail_attempts = fail_attempts
        self.delay_s = delay_s  # real awaitable delay, to exercise the query timeout
        self._attempts = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        return list(self.rows[0].keys()) if self.rows else []

    async def execute(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        sql: str,
        params: dict[str, Any],
        batch_size: int,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        self.calls.append((sql, dict(params)))
        if self._attempts < self.fail_attempts:
            self._attempts += 1
            raise TransientSourceError(ErrorCategory.SOURCE_UNREACHABLE, "simulated source outage")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        rows = self.rows
        if "watermark" in params:
            match = _WATERMARK_RE.search(sql)
            if match:
                column, op = match.group(1), match.group(2)
                bound = params["watermark"]
                ops = {
                    ">": lambda v: v > bound,
                    ">=": lambda v: v >= bound,
                    "<": lambda v: v < bound,
                    "<=": lambda v: v <= bound,
                    "=": lambda v: v == bound,
                }[op]
                rows = [r for r in rows if column in r and ops(_coerce_like(bound, r[column]))]
        for start in range(0, len(rows), batch_size):
            yield [dict(r) for r in rows[start : start + batch_size]]
