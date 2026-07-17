"""Integration: the REAL SQL Server and Oracle drivers against dockerized infra.

Speaks the real wire protocol (CONVENTIONS.md END STATE):

    sqlserver -> pymssql  vs. mcr.microsoft.com/mssql/server:2022 (testcontainers)
    oracle    -> oracledb vs. gvenzl/oracle-free                  (testcontainers)

Proves: real test-connection (ok / AUTH_FAILED, error scrubbed) and a real
query-ingestion pull that lands rows in the bronze object store, with the
watermark BOUND as a typed driver parameter — the recorded query shows only a
``:watermark`` placeholder, never the literal value (ING-FR-004/023/061, BR-5).

Auto-skips with a clear message when Docker is unavailable. SQL Server has no
arm64 image, so on Apple Silicon it runs under linux/amd64 emulation and is slow
to boot — the fixture allows a generous timeout.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.connectors import OracleConfig, SqlserverConfig
from app.domain.drivers.mssql import SqlServerProber, SqlServerQuerySource
from app.domain.drivers.oracle import OracleProber, OracleQuerySource
from tests.util import create_connection

pytestmark = pytest.mark.integration

# id, name, updated_at — the first row predates the initial watermark.
_ORDERS = [
    (1, "alpha", datetime(2026, 6, 30, tzinfo=UTC)),
    (2, "beta", datetime(2026, 7, 2, tzinfo=UTC)),
    (3, "gamma", datetime(2026, 7, 5, tzinfo=UTC)),
]


class _RecordingSource:
    """Wrap a real query source to capture (sql, params) — proves the watermark
    is a bound parameter, exactly like the fake's query log in AC-8."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def columns(self, config, secrets, statement) -> list[str]:
        return await self.inner.columns(config, secrets, statement)

    async def execute(self, config, secrets, sql, params, batch_size) -> AsyncIterator[list[dict]]:
        self.calls.append((sql, dict(params)))
        async for batch in self.inner.execute(config, secrets, sql, params, batch_size):
            yield batch


async def _assert_completed(client, auth, run_resp) -> str:
    data = run_resp.json()["data"]
    ingestion_id = data["ingestion_id"]
    if data["status"] != "completed":
        job = await client.get(f"/api/v1/ingestions/{ingestion_id}", headers=auth)
        raise AssertionError(f"job not completed: {job.json()['data'].get('error_log')}")
    return ingestion_id


# ===================================================================== SQL Server


@pytest.fixture(scope="session")
def mssql_server():
    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers not installed: {exc}")
    os.environ.setdefault("DOCKER_DEFAULT_PLATFORM", "linux/amd64")
    password = "Str0ng_P@ssw0rd!"
    try:
        container = (
            DockerContainer("mcr.microsoft.com/mssql/server:2022-latest")
            .with_env("ACCEPT_EULA", "Y")
            .with_env("MSSQL_SA_PASSWORD", password)
            .with_env("MSSQL_PID", "Developer")
            .with_exposed_ports(1433)
        )
        container.start()
        wait_for_logs(container, "SQL Server is now ready for client connections", timeout=240)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Docker/SQL Server unavailable — skipping SQL Server driver tests: {exc}")
    info = {
        "host": container.get_container_host_ip(),
        "port": int(container.get_exposed_port(1433)),
        "username": "sa",
        "password": password,
        "database": "master",
    }
    # The "ready" log fires before the SA login actually accepts connections
    # (pronounced under linux/amd64 emulation) — poll a real login first.
    _wait_for_login(info)
    yield info
    container.stop()


def _wait_for_login(info: dict, attempts: int = 40, delay_s: float = 3.0) -> None:
    import time

    import pymssql

    last: Exception | None = None
    for _ in range(attempts):
        try:
            conn = pymssql.connect(
                server=info["host"],
                port=str(info["port"]),
                user=info["username"],
                password=info["password"],
                database=info["database"],
                login_timeout=5,
            )
            conn.close()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay_s)
    pytest.skip(f"SQL Server never accepted a login: {last}")


def _seed_mssql(info: dict) -> None:
    import pymssql

    conn = pymssql.connect(
        server=info["host"],
        port=str(info["port"]),
        user=info["username"],
        password=info["password"],
        database=info["database"],
    )
    try:
        cur = conn.cursor()
        cur.execute("IF OBJECT_ID('dbo.orders','U') IS NOT NULL DROP TABLE dbo.orders")
        cur.execute("CREATE TABLE dbo.orders (id int, name varchar(64), updated_at datetime2)")
        cur.executemany(
            "INSERT INTO dbo.orders VALUES (%d, %s, %s)",
            [(i, n, ts.replace(tzinfo=None)) for i, n, ts in _ORDERS],
        )
        conn.commit()
    finally:
        conn.close()


async def test_sqlserver_probe_ok_and_auth_failed(mssql_server) -> None:
    cfg = SqlserverConfig(
        host=mssql_server["host"],
        port=mssql_server["port"],
        database=mssql_server["database"],
        username=mssql_server["username"],
    )
    prober = SqlServerProber(connect_timeout_s=30)

    ok = await prober.probe(cfg, {"password": mssql_server["password"]})
    assert ok.status == "ok", ok.error_detail

    bad = await prober.probe(cfg, {"password": "wrong-password"})
    assert bad.status == "failed"
    assert bad.error_category == "AUTH_FAILED"
    assert "wrong-password" not in (bad.error_detail or "")  # scrubbed (BR-1)


async def test_sqlserver_query_ingestion_pull_with_watermark(
    mssql_server, client, container, auth_a
) -> None:
    _seed_mssql(mssql_server)

    real = SqlServerQuerySource(connect_timeout_s=30, query_timeout_s=60)
    recorder = _RecordingSource(real)
    container.query_sources.set("sqlserver", recorder)

    src = await create_connection(
        client,
        auth_a,
        name="mssql-src",
        connector_type="sqlserver",
        config={
            "host": mssql_server["host"],
            "port": mssql_server["port"],
            "database": mssql_server["database"],
            "username": mssql_server["username"],
        },
        secrets={"password": mssql_server["password"]},
    )
    sched = (
        await client.post(
            "/api/v1/schedules",
            json={
                "connection_id": src["id"],
                "cron": "0 2 * * *",
                "timezone": "UTC",
                "ingestion_template": {
                    "ingestion_mode": "query",
                    "statement": "SELECT * FROM dbo.orders",
                    "new_dataset": {"name": "orders_mssql"},
                },
                "watermark": {
                    "column": "updated_at",
                    "operator": ">",
                    "value_type": "timestamp",
                    "initial_value": "2026-07-01T00:00:00Z",
                },
                "overlap_policy": "skip",
                "enabled": True,
            },
            headers=auth_a,
        )
    ).json()["data"]

    run1 = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    ingestion_id = await _assert_completed(client, auth_a, run1)
    sql1, params1 = recorder.calls[-1]
    assert sql1.endswith("src WHERE updated_at > :watermark")  # placeholder, not a literal
    assert "2026" not in sql1  # no splicing
    assert params1["watermark"] == datetime(2026, 7, 1, tzinfo=UTC)  # bound, typed
    job1 = await client.get(f"/api/v1/ingestions/{ingestion_id}", headers=auth_a)
    assert job1.json()["data"]["rows_appended"] == 2  # only the 2 rows past the watermark


# ===================================================================== Oracle


@pytest.fixture(scope="session")
def oracle_server():
    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers not installed: {exc}")
    password = "OraFree_pw1"
    try:
        container = (
            DockerContainer("gvenzl/oracle-free:slim-faststart")
            .with_env("ORACLE_PASSWORD", password)
            .with_exposed_ports(1521)
        )
        container.start()
        wait_for_logs(container, "DATABASE IS READY TO USE", timeout=240)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Docker/Oracle unavailable — skipping Oracle driver tests: {exc}")
    info = {
        "host": container.get_container_host_ip(),
        "port": int(container.get_exposed_port(1521)),
        "username": "system",
        "password": password,
        "service_name": "FREEPDB1",
    }
    yield info
    container.stop()


def _seed_oracle(info: dict) -> None:
    import oracledb

    conn = oracledb.connect(
        user=info["username"],
        password=info["password"],
        dsn=f"{info['host']}:{info['port']}/{info['service_name']}",
    )
    try:
        cur = conn.cursor()
        try:
            cur.execute("DROP TABLE orders")
        except oracledb.DatabaseError:
            pass
        cur.execute(
            "CREATE TABLE orders "
            "(id NUMBER, name VARCHAR2(64), updated_at TIMESTAMP WITH TIME ZONE)"
        )
        cur.executemany("INSERT INTO orders VALUES (:1, :2, :3)", _ORDERS)
        conn.commit()
    finally:
        conn.close()


async def test_oracle_probe_ok_and_auth_failed(oracle_server) -> None:
    cfg = OracleConfig(
        host=oracle_server["host"],
        port=oracle_server["port"],
        service_name=oracle_server["service_name"],
        username=oracle_server["username"],
    )
    prober = OracleProber(connect_timeout_s=30)

    ok = await prober.probe(cfg, {"password": oracle_server["password"]})
    assert ok.status == "ok", ok.error_detail

    bad = await prober.probe(cfg, {"password": "wrong-password"})
    assert bad.status == "failed"
    assert bad.error_category == "AUTH_FAILED"
    assert "wrong-password" not in (bad.error_detail or "")  # scrubbed (BR-1)


async def test_oracle_query_ingestion_pull_with_watermark(
    oracle_server, client, container, auth_a
) -> None:
    _seed_oracle(oracle_server)

    real = OracleQuerySource(connect_timeout_s=30, query_timeout_s=60)
    recorder = _RecordingSource(real)
    container.query_sources.set("oracle", recorder)

    src = await create_connection(
        client,
        auth_a,
        name="oracle-src",
        connector_type="oracle",
        config={
            "host": oracle_server["host"],
            "port": oracle_server["port"],
            "service_name": oracle_server["service_name"],
            "username": oracle_server["username"],
        },
        secrets={"password": oracle_server["password"]},
    )
    sched = (
        await client.post(
            "/api/v1/schedules",
            json={
                "connection_id": src["id"],
                "cron": "0 2 * * *",
                "timezone": "UTC",
                "ingestion_template": {
                    "ingestion_mode": "query",
                    # Oracle folds unquoted identifiers to upper case.
                    "statement": "SELECT ID, NAME, UPDATED_AT FROM orders",
                    "new_dataset": {"name": "orders_oracle"},
                },
                "watermark": {
                    "column": "UPDATED_AT",
                    "operator": ">",
                    "value_type": "timestamp",
                    "initial_value": "2026-07-01T00:00:00Z",
                },
                "overlap_policy": "skip",
                "enabled": True,
            },
            headers=auth_a,
        )
    ).json()["data"]

    run1 = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    ingestion_id = await _assert_completed(client, auth_a, run1)
    sql1, params1 = recorder.calls[-1]
    assert sql1.endswith("src WHERE UPDATED_AT > :watermark")  # placeholder, not a literal
    assert "2026" not in sql1  # no splicing
    assert params1["watermark"] == datetime(2026, 7, 1, tzinfo=UTC)  # bound, typed
    job1 = await client.get(f"/api/v1/ingestions/{ingestion_id}", headers=auth_a)
    assert job1.json()["data"]["rows_appended"] == 2
