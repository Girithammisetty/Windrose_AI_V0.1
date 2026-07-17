"""Contract tests for the credential-gated cloud/SaaS query drivers.

Each adapter drives a REAL vendor SDK / HTTP protocol, but live end-to-end
verification needs customer credentials (see README). These tests substitute a
mocked transport (a fake DB-API connection, a fake warehouse client, or an
``httpx.MockTransport``) to exercise the adapter's request/response shaping
offline — proving in particular that the watermark rides as a **bound driver
parameter** (placeholder-only SQL, typed value out-of-band), never spliced.

The live counterparts are in ``tests/integration/test_cloud_drivers_live.py``,
which skip-with-reason unless the connector's credentials are present.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from app.domain.connectors import (
    BigqueryConfig,
    DatabricksConfig,
    RedshiftConfig,
    SalesforceConfig,
    SnowflakeConfig,
    SpannerConfig,
)
from app.domain.drivers import bigquery as bq
from app.domain.drivers import spanner as sp
from app.domain.drivers.databricks import databricks_dialect
from app.domain.drivers.dbapi import DbapiQuerySource
from app.domain.drivers.redshift import redshift_dialect
from app.domain.drivers.salesforce import (
    SalesforceQuerySource,
    build_soql,
    soql_datetime_literal,
)
from app.domain.drivers.snowflake import snowflake_dialect
from app.domain.watermark import WatermarkSpec, build_incremental_query

WM = datetime(2026, 7, 1, tzinfo=UTC)
WRAPPED_SQL, WRAPPED_PARAMS = build_incremental_query(
    "SELECT * FROM orders",
    WatermarkSpec(
        column="updated_at", operator=">", value_type="timestamp", value="2026-07-01T00:00:00Z"
    ),
)


async def _collect(source, config, secrets, sql, params, batch=10):
    out = []
    async for b in source.execute(config, secrets, sql, params, batch):
        out.extend(b)
    return out


# --------------------------------------------------------------- DB-API fakes


class _FakeCursor:
    def __init__(self, rows, description):
        self.rows = rows
        self.description = description
        self.executed: list[tuple] = []
        self._pos = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._pos = 0

    def fetchmany(self, n):
        batch = self.rows[self._pos : self._pos + n]
        self._pos += n
        return batch

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


DBAPI_CASES = [
    (
        "snowflake",
        snowflake_dialect,
        SnowflakeConfig(account="a", username="u", warehouse="w", database="d"),
        "%(watermark)s",
        dict,
    ),
    (
        "redshift",
        redshift_dialect,
        RedshiftConfig(host="h", database="d", username="u"),
        "%s",
        list,
    ),
    (
        "databricks",
        databricks_dialect,
        DatabricksConfig(server_hostname="h", http_path="/p"),
        "%(watermark)s",
        dict,
    ),
]


@pytest.mark.parametrize(("name", "factory", "config", "placeholder", "params_kind"), DBAPI_CASES)
async def test_dbapi_warehouse_binds_watermark_as_parameter(
    name, factory, config, placeholder, params_kind
) -> None:
    cursor = _FakeCursor(
        rows=[{"id": 1, "updated_at": "2026-07-02T00:00:00+00:00"}],
        description=[("id",), ("updated_at",)],
    )
    dialect = replace(factory(), connect=lambda cfg, sec, to: _FakeConn(cursor))
    source = DbapiQuerySource(dialect)

    rows = await _collect(
        source, config, {"password": "p", "access_token": "t"}, WRAPPED_SQL, WRAPPED_PARAMS
    )
    assert rows == [{"id": 1, "updated_at": "2026-07-02T00:00:00+00:00"}]

    sent_sql, sent_params = cursor.executed[-1]
    # placeholder-only SQL — the value never enters the query text
    assert sent_sql.endswith(f"WHERE updated_at > {placeholder}")
    assert "2026" not in sent_sql
    # typed value carried out-of-band, in the driver's native param container
    assert isinstance(sent_params, params_kind)
    bound = sent_params["watermark"] if params_kind is dict else sent_params[0]
    assert bound == WM


# --------------------------------------------------------------- BigQuery


class _FakeWarehouseClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple] = []

    def run(self, sql, specs, batch_size):
        self.calls.append((sql, specs))
        yield from self.rows

    def probe(self):
        self.calls.append(("SELECT 1", []))

    def close(self):
        pass


async def test_bigquery_binds_watermark_as_typed_query_parameter() -> None:
    fake = _FakeWarehouseClient([{"id": 1, "updated_at": WM}])
    source = bq.BigQueryQuerySource(client_factory=lambda c, s, t: fake)
    config = BigqueryConfig(project_id="p", dataset="d")

    rows = await _collect(source, config, {}, WRAPPED_SQL, WRAPPED_PARAMS)
    assert rows == [{"id": 1, "updated_at": WM}]

    sent_sql, specs = fake.calls[-1]
    assert sent_sql.endswith("WHERE updated_at > @watermark")  # native named param
    assert "2026" not in sent_sql
    assert len(specs) == 1
    assert specs[0].name == "watermark" and specs[0].type == "TIMESTAMP" and specs[0].value == WM


def test_bigquery_type_inference() -> None:
    assert bq._bq_type(WM) == "TIMESTAMP"
    assert bq._bq_type(5) == "INT64"
    assert bq._bq_type(True) == "BOOL"
    assert bq._bq_type("x") == "STRING"


# --------------------------------------------------------------- Spanner


async def test_spanner_binds_watermark_as_typed_query_parameter() -> None:
    fake = _FakeWarehouseClient([{"id": 1}])
    source = sp.SpannerQuerySource(client_factory=lambda c, s, t: fake)
    config = SpannerConfig(project_id="p", instance_id="i", database="d")

    rows = await _collect(source, config, {}, WRAPPED_SQL, WRAPPED_PARAMS)
    assert rows == [{"id": 1}]
    sent_sql, specs = fake.calls[-1]
    assert sent_sql.endswith("WHERE updated_at > @watermark")
    assert "2026" not in sent_sql
    assert specs[0].name == "watermark" and specs[0].type == "TIMESTAMP" and specs[0].value == WM


# --------------------------------------------------------------- Salesforce


def _sf_transport(recorder: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        if request.url.path.endswith("/services/oauth2/token"):
            return httpx.Response(
                200, json={"access_token": "tok", "instance_url": "https://na1.salesforce.com"}
            )
        # first query page -> one record + a next page; second page -> done
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "done": False,
                    "nextRecordsUrl": "/services/data/v59.0/query/01g-2000",
                    "records": [{"attributes": {"type": "Account"}, "Id": "001", "Name": "Acme"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "done": True,
                "records": [{"attributes": {"type": "Account"}, "Id": "002", "Name": "Globex"}],
            },
        )

    return httpx.MockTransport(handler)


async def test_salesforce_soql_incremental_and_pagination() -> None:
    recorder: list[httpx.Request] = []
    source = SalesforceQuerySource(transport=_sf_transport(recorder))
    config = SalesforceConfig(username="u@acme.com")
    secrets = {
        "password": "pw",
        "security_token": "tok",
        "client_id": "cid",
        "client_secret": "csecret",
    }

    wrapped, params = build_incremental_query(
        "SELECT Id, Name FROM Account",
        WatermarkSpec(
            column="SystemModstamp",
            operator=">",
            value_type="timestamp",
            value="2026-07-01T00:00:00Z",
        ),
    )
    rows = await _collect(source, config, secrets, wrapped, params)
    # both pages drained (pagination via nextRecordsUrl); attributes stripped
    assert rows == [{"Id": "001", "Name": "Acme"}, {"Id": "002", "Name": "Globex"}]

    query_reqs = [r for r in recorder if r.url.path.endswith("/query")]
    soql = query_reqs[0].url.params["q"]
    # SOQL has no bind facility: the typed datetime is rendered as the canonical
    # SystemModstamp literal (unquoted ISO-8601), never as free text.
    assert "SystemModstamp > 2026-07-01T00:00:00Z" in soql
    assert ":watermark" not in soql


def test_salesforce_literal_rejects_untyped_value() -> None:
    assert soql_datetime_literal(WM) == "2026-07-01T00:00:00Z"
    with pytest.raises(ValueError, match="typed datetime"):
        soql_datetime_literal("2026-07-01' OR '1'='1")  # injection attempt rejected


def test_salesforce_build_soql_appends_condition() -> None:
    wrapped, params = build_incremental_query(
        "SELECT Id FROM Account WHERE IsDeleted = false",
        WatermarkSpec(
            column="SystemModstamp",
            operator=">",
            value_type="timestamp",
            value="2026-07-01T00:00:00Z",
        ),
    )
    soql = build_soql(wrapped, params)
    assert soql == (
        "SELECT Id FROM Account WHERE IsDeleted = false AND SystemModstamp > 2026-07-01T00:00:00Z"
    )
    # a plain (non-incremental) statement passes straight through
    assert build_soql("SELECT Id FROM Account", {}) == "SELECT Id FROM Account"
