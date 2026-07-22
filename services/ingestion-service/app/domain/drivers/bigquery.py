"""Real Google BigQuery driver (google-cloud-bigquery).

Credential-gated: the adapter drives the real BigQuery SDK but a live pull needs
GCP service-account credentials. The watermark is bound as a **typed named query
parameter** (``@watermark`` + a ``ScalarQueryParameter``) — BigQuery's native
parameterization — so the value is never spliced into SQL text (ING-FR-061,
BR-5). Results stream page-by-page via the row iterator (ING-FR-023).

The BigQuery ``Client`` is reached through an injectable ``client_factory`` so a
contract test can substitute a fake transport that records the query + parameter
specs and returns canned rows, without touching the network or requiring GCP
credentials. The runtime default lazily imports and drives the real SDK.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.domain.drivers.sql import quote_identifier, to_at_named, wrap_limit_zero
from app.domain.errors import ErrorCategory, TransientSourceError
from app.domain.probers import PreviewResult, ProbeResult


@dataclass(slots=True)
class ParamSpec:
    """A typed, out-of-band query parameter (never spliced into SQL text)."""

    name: str
    type: str
    value: Any


def _bq_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, datetime):
        return "TIMESTAMP"
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, Decimal):
        return "NUMERIC"
    if isinstance(value, float):
        return "FLOAT64"
    return "STRING"


def plan_query(sql: str, params: dict[str, Any]) -> tuple[str, list[ParamSpec]]:
    """Return (``@name`` SQL, typed param specs) — the value stays out-of-band."""
    bq_sql, used = to_at_named(sql, params)
    specs = [ParamSpec(name=k, type=_bq_type(v), value=v) for k, v in used.items()]
    return bq_sql, specs


class BigQueryClient:
    """Thin adapter over ``google.cloud.bigquery.Client`` (lazy SDK import)."""

    def __init__(self, config: BaseModel, secrets: dict[str, str], timeout: float) -> None:
        from google.cloud import bigquery
        from google.oauth2 import service_account

        self._bigquery = bigquery
        self._timeout = timeout
        credentials = None
        if secrets.get("credentials_json"):
            info = json.loads(secrets["credentials_json"])
            credentials = service_account.Credentials.from_service_account_info(info)
        self._client = bigquery.Client(project=config.project_id, credentials=credentials)

    def _job_config(self, specs: list[ParamSpec]):
        bq = self._bigquery
        return bq.QueryJobConfig(
            query_parameters=[bq.ScalarQueryParameter(s.name, s.type, s.value) for s in specs]
        )

    def run(self, sql: str, specs: list[ParamSpec], batch_size: int):
        job = self._client.query(sql, job_config=self._job_config(specs), timeout=self._timeout)
        result = job.result(page_size=batch_size)
        columns = [f.name for f in result.schema]
        for row in result:
            yield {c: row[c] for c in columns}

    def probe(self) -> None:
        job = self._client.query("SELECT 1", timeout=self._timeout)
        list(job.result())

    def close(self) -> None:
        self._client.close()


ClientFactory = Callable[[BaseModel, dict[str, str], float], Any]


def _default_factory(config: BaseModel, secrets: dict[str, str], timeout: float) -> Any:
    return BigQueryClient(config, secrets, timeout)


def _classify(exc: Exception) -> tuple[str, str]:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT, "query timed out"
    if "denied" in text or "credential" in text or "unauthor" in text or "forbidden" in text:
        return ErrorCategory.AUTH_FAILED, "authentication failed (scrubbed)"
    return ErrorCategory.SOURCE_UNREACHABLE, "bigquery request failed (scrubbed)"


class BigQueryProber:
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
            _safe_close(client)

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


class BigQueryPreviewer:
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
            _safe_close(client)
        columns = list(rows[0].keys()) if rows else []
        return PreviewResult(columns=columns, rows=rows)

    async def preview(
        self, config: BaseModel, secrets: dict[str, str], request: dict[str, Any], limit: int
    ) -> PreviewResult:
        target = request.get("query") or (
            f"SELECT * FROM {quote_identifier(request['table'], quote='`')}"
            if request.get("table") else None
        )
        if not target:
            raise ValueError("preview requires table or query")
        return await asyncio.to_thread(self._preview_sync, config, secrets, target, limit)


class BigQueryQuerySource:
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
            _safe_close(client)

    async def columns(
        self, config: BaseModel, secrets: dict[str, str], statement: str
    ) -> list[str]:
        # BigQuery reports the result schema even for a 0-row LIMIT; fall back to
        # an empty list if the fake/real transport yields no rows.
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
        bq_sql, specs = plan_query(sql, params)
        try:
            client = await asyncio.to_thread(self._factory, config, secrets, self.query_timeout_s)
        except Exception as exc:  # noqa: BLE001
            category, detail = _classify(exc)
            raise TransientSourceError(category, detail) from exc

        def _drain() -> list[list[dict[str, Any]]]:
            batches: list[list[dict[str, Any]]] = []
            batch: list[dict[str, Any]] = []
            for row in client.run(bq_sql, specs, batch_size):
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
            await asyncio.to_thread(_safe_close, client)


def _safe_close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
