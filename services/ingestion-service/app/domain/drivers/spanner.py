"""Real Google Cloud Spanner driver (google-cloud-spanner).

Credential-gated for production (needs a real Spanner instance + GCP creds), but
also runnable against the **Spanner emulator** locally (set
``SPANNER_EMULATOR_HOST``) — the injectable ``client_factory`` makes both the
emulator and a contract-test fake first-class.

Spanner supports native named parameters: the watermark binds as ``@watermark``
with a typed ``param_type`` — the value is never spliced into SQL text
(ING-FR-061, BR-5). Reads stream row-by-row from a read-only snapshot
(ING-FR-023).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.domain.drivers.bigquery import ParamSpec, _bq_type  # shared typed-param spec
from app.domain.drivers.sql import to_at_named, wrap_limit_zero
from app.domain.errors import ErrorCategory, TransientSourceError
from app.domain.probers import PreviewResult, ProbeResult


def plan_query(sql: str, params: dict[str, Any]) -> tuple[str, list[ParamSpec]]:
    """Return (``@name`` SQL, typed param specs); value stays out-of-band."""
    at_sql, used = to_at_named(sql, params)
    return at_sql, [ParamSpec(name=k, type=_bq_type(v), value=v) for k, v in used.items()]


class SpannerClient:
    """Thin adapter over a Spanner ``Database`` snapshot (lazy SDK import)."""

    def __init__(self, config: BaseModel, secrets: dict[str, str], timeout: float) -> None:
        from google.cloud import spanner

        self._spanner = spanner
        self._timeout = timeout
        credentials = None
        if os.getenv("SPANNER_EMULATOR_HOST"):
            from google.auth.credentials import AnonymousCredentials

            credentials = AnonymousCredentials()
        elif secrets.get("credentials_json"):
            from google.oauth2 import service_account

            info = json.loads(secrets["credentials_json"])
            credentials = service_account.Credentials.from_service_account_info(info)
        client = spanner.Client(project=config.project_id, credentials=credentials)
        instance = client.instance(config.instance_id)
        self._database = instance.database(config.database)

    def _param_types(self, specs: list[ParamSpec]) -> dict[str, Any]:
        pt = self._spanner.param_types
        mapping = {
            "STRING": pt.STRING,
            "INT64": pt.INT64,
            "FLOAT64": pt.FLOAT64,
            "NUMERIC": pt.NUMERIC,
            "BOOL": pt.BOOL,
            "TIMESTAMP": pt.TIMESTAMP,
            "DATE": pt.DATE,
        }
        return {s.name: mapping.get(s.type, pt.STRING) for s in specs}

    def run(self, sql: str, specs: list[ParamSpec], batch_size: int):
        params = {s.name: s.value for s in specs}
        with self._database.snapshot() as snapshot:
            result = snapshot.execute_sql(
                sql, params=params or None, param_types=self._param_types(specs) or None
            )
            # StreamedResultSet.fields is only populated once the first result
            # chunk has streamed in — reading it before iterating raises
            # AttributeError ('_metadata' is None). Resolve column names lazily
            # after the first row so an empty result set yields nothing cleanly.
            columns: list[str] | None = None
            for row in result:
                if columns is None:
                    columns = [f.name for f in result.fields]
                yield dict(zip(columns, row, strict=False))

    def probe(self) -> None:
        with self._database.snapshot() as snapshot:
            list(snapshot.execute_sql("SELECT 1"))

    def close(self) -> None:  # snapshots are context-managed; nothing to close
        return None


ClientFactory = Callable[[BaseModel, dict[str, str], float], Any]


def _default_factory(config: BaseModel, secrets: dict[str, str], timeout: float) -> Any:
    return SpannerClient(config, secrets, timeout)


def _classify(exc: Exception) -> tuple[str, str]:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT, "query timed out"
    if "denied" in text or "credential" in text or "unauthor" in text or "permission" in text:
        return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
    return ErrorCategory.SOURCE_UNREACHABLE, "spanner request failed (scrubbed)"


class SpannerProber:
    def __init__(
        self, *, client_factory: ClientFactory = _default_factory, connect_timeout_s: float = 15.0
    ) -> None:
        self._factory = client_factory
        self.connect_timeout_s = connect_timeout_s

    def _probe_sync(self, config: BaseModel, secrets: dict[str, str]) -> None:
        client = self._factory(config, secrets, self.connect_timeout_s)
        try:
            client.probe()
        finally:
            client.close()

    async def probe(self, config: BaseModel, secrets: dict[str, str]) -> ProbeResult:
        started = time.monotonic()
        try:
            await asyncio.to_thread(self._probe_sync, config, secrets)
        except Exception as exc:  # noqa: BLE001
            category, detail = _classify(exc)
            return ProbeResult(
                "failed",
                int((time.monotonic() - started) * 1000),
                error_category=category,
                error_detail=detail,
            )
        return ProbeResult("ok", int((time.monotonic() - started) * 1000))


class SpannerPreviewer:
    def __init__(
        self, *, client_factory: ClientFactory = _default_factory, connect_timeout_s: float = 15.0
    ) -> None:
        self._factory = client_factory
        self.connect_timeout_s = connect_timeout_s

    def _preview_sync(
        self, config: BaseModel, secrets: dict[str, str], target: str, limit: int
    ) -> PreviewResult:
        client = self._factory(config, secrets, self.connect_timeout_s)
        try:
            rows = list(
                client.run(f"SELECT * FROM ({target.rstrip(';')}) _p LIMIT {int(limit)}", [], limit)
            )
        finally:
            client.close()
        columns = list(rows[0].keys()) if rows else []
        return PreviewResult(columns=columns, rows=rows)

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        target = request.get("query") or (
            f"SELECT * FROM {request['table']}" if request.get("table") else None
        )
        if not target:
            raise ValueError("preview requires table or query")
        return await asyncio.to_thread(self._preview_sync, config, secrets, target, limit)


class SpannerQuerySource:
    def __init__(
        self,
        *,
        client_factory: ClientFactory = _default_factory,
        connect_timeout_s: float = 15.0,
        query_timeout_s: float = 1600.0,
    ) -> None:
        self._factory = client_factory
        self.connect_timeout_s = connect_timeout_s
        self.query_timeout_s = query_timeout_s

    def _columns_sync(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        client = self._factory(config, secrets, self.connect_timeout_s)
        try:
            rows = list(client.run(wrap_limit_zero(statement), [], 1))
            return list(rows[0].keys()) if rows else []
        finally:
            client.close()

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        try:
            return await asyncio.to_thread(self._columns_sync, config, secrets, statement)
        except Exception as exc:  # noqa: BLE001
            category, detail = _classify(exc)
            raise TransientSourceError(category, f"column introspection failed: {detail}") from exc

    async def execute(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        sql: str,
        params: dict[str, Any],
        batch_size: int,
    ):
        at_sql, specs = plan_query(sql, params)
        try:
            client = await asyncio.to_thread(self._factory, config, secrets, self.query_timeout_s)
        except Exception as exc:  # noqa: BLE001
            category, detail = _classify(exc)
            raise TransientSourceError(category, detail) from exc

        def _drain() -> list[list[dict[str, Any]]]:
            batches: list[list[dict[str, Any]]] = []
            batch: list[dict[str, Any]] = []
            for row in client.run(at_sql, specs, batch_size):
                batch.append(dict(row))
                if len(batch) >= batch_size:
                    batches.append(batch)
                    batch = []
            if batch:
                batches.append(batch)
            return batches

        try:
            for batch in await asyncio.to_thread(_drain):
                yield batch
        finally:
            await asyncio.to_thread(client.close)
