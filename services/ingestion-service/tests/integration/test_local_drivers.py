"""Integration: the REAL local-protocol connection drivers.

Speaks the real wire protocol against dockerized infra (CONVENTIONS.md END
STATE, table row "Connection drivers (JDBC/SFTP/HTTP)"):

    postgres  -> asyncpg vs. a real Postgres  (testcontainers)
    mysql     -> aiomysql vs. a real MySQL     (testcontainers)
    sftp      -> asyncssh vs. a real SFTP server (atmoz/sftp)
    http_api  -> httpx vs. a real HTTP server   (loopback fixture)

Proves: real test-connection (ok / AUTH_FAILED), real query-ingestion pull with
rows landing in the object store (parquet bronze) + a correct watermark advance
across runs with the value BOUND as a typed driver parameter (no splicing — the
recorded query log shows only a `:watermark` placeholder), and real SFTP/HTTP
file fetches streamed into the object store.

Auto-skips with a clear message when Docker is unavailable.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import asyncssh
import pytest

from app.domain.connectors import HttpApiConfig, MysqlConfig, PostgresConfig, SftpConfig
from app.domain.drivers.http import HttpProber, HttpSourceFetcher
from app.domain.drivers.mysql import MysqlProber, MysqlQuerySource
from app.domain.drivers.postgres import PostgresProber, PostgresQuerySource
from app.domain.drivers.sftp import SftpProber, SftpSourceFetcher, SftpSourcePreviewer
from app.domain.objectstore import LocalFSObjectStore
from tests.util import create_connection

pytestmark = pytest.mark.integration

_ORDERS = [
    (1, "alpha", "2026-06-30T00:00:00+00:00"),  # before the initial watermark
    (2, "beta", "2026-07-02T00:00:00+00:00"),
    (3, "gamma", "2026-07-05T00:00:00+00:00"),
]


# --------------------------------------------------------------------------- utils


class _RecordingSource:
    """Wraps a real query source to capture (sql, params) — proves the watermark
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


def _parse(url: str) -> dict[str, Any]:
    # Strip a "+driver" suffix from the scheme only (e.g. "mysql+pymysql://...");
    # plain "mysql://..." (no "+") must be left as-is rather than re-appended,
    # which previously duplicated the URL and corrupted the parsed database name.
    scheme, sep, rest = url.partition("://")
    p = urlparse(f"{scheme.split('+', 1)[0]}{sep}{rest}")
    return {
        "host": p.hostname,
        "port": p.port,
        "username": p.username,
        "password": p.password,
        "database": p.path.lstrip("/"),
    }


# --------------------------------------------------------------------------- MySQL fixture


@pytest.fixture(scope="session")
def mysql_container():
    try:
        from testcontainers.mysql import MySqlContainer
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers[mysql] not installed: {exc}")
    try:
        container = MySqlContainer("mysql:8.0")
        container.start()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Docker unavailable — skipping MySQL driver tests: {exc}")
    yield container
    container.stop()


# --------------------------------------------------------------------------- SFTP fixture


@pytest.fixture(scope="session")
def sftp_server():
    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers not installed: {exc}")
    try:
        container = (
            DockerContainer("atmoz/sftp:alpine")
            .with_command("foo:pass:::upload")
            .with_exposed_ports(22)
        )
        container.start()
        wait_for_logs(container, "Server listening on", timeout=40)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Docker unavailable — skipping SFTP driver tests: {exc}")
    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(22))
    yield {"host": host, "port": port, "username": "foo", "password": "pass"}
    container.stop()


# --------------------------------------------------------------------------- HTTP fixture

_HTTP_BODY = b"id,name\n1,alpha\n2,beta\n3,gamma\n"


class _CsvHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(_HTTP_BODY)))
        self.end_headers()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(_HTTP_BODY)))
        self.end_headers()
        self.wfile.write(_HTTP_BODY)

    def log_message(self, *args):  # silence
        return


@pytest.fixture()
def http_source():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CsvHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield {"url": f"http://{host}:{port}/data.csv"}
    server.shutdown()


# ===================================================================== Postgres


async def test_postgres_probe_ok_and_auth_failed(pg_container) -> None:
    """Real test-connection against dockerized Postgres (ING-FR-004)."""
    conn = _parse(pg_container.get_connection_url())
    cfg = PostgresConfig(
        host=conn["host"],
        port=conn["port"],
        database=conn["database"],
        username=conn["username"],
        ssl_mode="disable",
    )
    prober = PostgresProber(connect_timeout_s=10)

    ok = await prober.probe(cfg, {"password": conn["password"]})
    assert ok.status == "ok", ok.error_detail

    bad = await prober.probe(cfg, {"password": "wrong-password"})
    assert bad.status == "failed"
    assert bad.error_category == "AUTH_FAILED"
    assert "wrong-password" not in (bad.error_detail or "")  # scrubbed (BR-1)


async def test_postgres_query_ingestion_pull_with_watermark(
    pg_container, client, container, auth_a
) -> None:
    """Real streaming pull + rows land in the bronze object store, with the
    watermark bound as a typed parameter that advances across runs (AC-8)."""
    import psycopg

    conn = _parse(pg_container.get_connection_url())
    with psycopg.connect(pg_container.get_connection_url().replace("+psycopg2", "")) as db:
        with db.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS public.orders")
            cur.execute("CREATE TABLE public.orders (id int, name text, updated_at timestamptz)")
            cur.executemany("INSERT INTO public.orders VALUES (%s, %s, %s)", _ORDERS)
        db.commit()

    real = PostgresQuerySource(connect_timeout_s=10, query_timeout_s=60)
    recorder = _RecordingSource(real)
    container.query_sources.set("postgres", recorder)

    src = await create_connection(
        client,
        auth_a,
        name="pg-src",
        config={
            "host": conn["host"],
            "port": conn["port"],
            "database": conn["database"],
            "username": conn["username"],
            "ssl_mode": "disable",
        },
        secrets={"password": conn["password"]},
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
                    "statement": "SELECT * FROM public.orders",
                    "new_dataset": {"name": "orders"},
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

    # run 1: appends only the 2 rows newer than the initial watermark
    run1 = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    assert run1.json()["data"]["status"] == "completed", run1.text
    sql1, params1 = recorder.calls[-1]
    assert sql1.endswith("src WHERE updated_at > :watermark")  # placeholder, not a literal
    assert "2026" not in sql1  # no splicing
    assert params1["watermark"] == datetime(2026, 7, 1, tzinfo=UTC)  # bound, typed
    job1 = await client.get(
        f"/api/v1/ingestions/{run1.json()['data']['ingestion_id']}", headers=auth_a
    )
    assert job1.json()["data"]["rows_appended"] == 2

    # source gains a newer row; run 2 binds the max watermark observed in run 1
    with psycopg.connect(pg_container.get_connection_url().replace("+psycopg2", "")) as db:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO public.orders VALUES (4, 'delta', '2026-07-08T00:00:00+00:00')"
            )
        db.commit()

    run2 = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    assert run2.json()["data"]["status"] == "completed", run2.text
    _, params2 = recorder.calls[-1]
    assert params2["watermark"] == datetime(2026, 7, 5, tzinfo=UTC)  # advanced, still typed
    job2 = await client.get(
        f"/api/v1/ingestions/{run2.json()['data']['ingestion_id']}", headers=auth_a
    )
    assert job2.json()["data"]["rows_appended"] == 1  # only the newest row

    state = await client.get(f"/api/v1/schedules/{sched['id']}", headers=auth_a)
    assert state.json()["data"]["watermark"]["current_value"] == "2026-07-08T00:00:00+00:00"


# ===================================================================== MySQL


async def test_mysql_probe_ok_and_auth_failed(mysql_container) -> None:
    conn = _parse(mysql_container.get_connection_url())
    cfg = MysqlConfig(
        host=conn["host"], port=conn["port"], database=conn["database"], username=conn["username"]
    )
    prober = MysqlProber(connect_timeout_s=10)

    ok = await prober.probe(cfg, {"password": conn["password"]})
    assert ok.status == "ok", ok.error_detail

    bad = await prober.probe(cfg, {"password": "wrong-password"})
    assert bad.status == "failed"
    assert bad.error_category == "AUTH_FAILED"
    assert "wrong-password" not in (bad.error_detail or "")


async def test_mysql_query_ingestion_pull_with_watermark(
    mysql_container, client, container, auth_a
) -> None:
    import pymysql

    conn = _parse(mysql_container.get_connection_url())
    db = pymysql.connect(
        host=conn["host"],
        port=conn["port"],
        user=conn["username"],
        password=conn["password"],
        database=conn["database"],
    )
    with db.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS orders")
        cur.execute("CREATE TABLE orders (id int, name varchar(64), updated_at datetime)")
        cur.executemany(
            "INSERT INTO orders VALUES (%s, %s, %s)",
            [(i, n, ts.replace("T", " ").replace("+00:00", "")) for i, n, ts in _ORDERS],
        )
    db.commit()
    db.close()

    real = MysqlQuerySource(connect_timeout_s=10, query_timeout_s=60)
    recorder = _RecordingSource(real)
    container.query_sources.set("mysql", recorder)

    src = await create_connection(
        client,
        auth_a,
        name="mysql-src",
        connector_type="mysql",
        config={
            "host": conn["host"],
            "port": conn["port"],
            "database": conn["database"],
            "username": conn["username"],
        },
        secrets={"password": conn["password"]},
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
                    "statement": "SELECT * FROM orders",
                    "new_dataset": {"name": "orders_my"},
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
    assert run1.json()["data"]["status"] == "completed", run1.text
    sql1, params1 = recorder.calls[-1]
    assert sql1.endswith("src WHERE updated_at > :watermark")
    assert "2026" not in sql1  # no splicing
    assert params1["watermark"] == datetime(2026, 7, 1, tzinfo=UTC)
    job1 = await client.get(
        f"/api/v1/ingestions/{run1.json()['data']['ingestion_id']}", headers=auth_a
    )
    assert job1.json()["data"]["rows_appended"] == 2


# ===================================================================== SFTP


async def test_sftp_probe_and_file_fetch_to_object_store(sftp_server, tmp_path) -> None:
    """Real SFTP LIST probe + streaming file fetch into the object store
    (ING-FR-004/041)."""
    cfg = SftpConfig(
        host=sftp_server["host"],
        port=sftp_server["port"],
        username=sftp_server["username"],
        root_directory="/upload",
    )
    secrets = {"password": sftp_server["password"]}

    # seed a file on the real SFTP server
    payload = b"id,name\n1,alpha\n2,beta\n"
    async with asyncssh.connect(
        host=sftp_server["host"],
        port=sftp_server["port"],
        username=sftp_server["username"],
        password=sftp_server["password"],
        known_hosts=None,
    ) as ssh:
        async with ssh.start_sftp_client() as sftp:
            async with sftp.open("/upload/data.csv", "wb") as fh:
                await fh.write(payload)

    # probe (LIST)
    probe = await SftpProber(connect_timeout_s=10).probe(cfg, secrets)
    assert probe.status == "ok", probe.error_detail

    # preview lists the directory
    preview = await SftpSourcePreviewer(connect_timeout_s=10).preview(
        cfg, secrets, {"path": "/upload"}, 100
    )
    assert {"name": "data.csv"} in preview.rows

    # fetch the file, streamed into the object store
    store = LocalFSObjectStore(tmp_path / "objects")
    result = await SftpSourceFetcher(connect_timeout_s=10).fetch(
        cfg, secrets, {"path": "/upload/data.csv"}, store, "sftp/data.csv"
    )
    assert result.size == len(payload)

    read = b""
    async for chunk in store.open_stream("sftp/data.csv"):
        read += chunk
    assert read == payload


# ===================================================================== HTTP


async def test_http_probe_and_fetch_to_object_store(http_source, tmp_path) -> None:
    """Real httpx HEAD/GET probe + streaming fetch into the object store."""
    cfg = HttpApiConfig(method="GET", url=http_source["url"])

    probe = await HttpProber(connect_timeout_s=10).probe(cfg, {})
    assert probe.status == "ok", probe.error_detail

    store = LocalFSObjectStore(tmp_path / "objects")
    result = await HttpSourceFetcher(timeout_s=30).fetch(cfg, {}, {}, store, "http/data.csv")
    assert result.size == len(_HTTP_BODY)

    read = b""
    async for chunk in store.open_stream("http/data.csv"):
        read += chunk
    assert read == _HTTP_BODY


async def test_http_probe_unreachable_is_source_unreachable() -> None:
    cfg = HttpApiConfig(method="GET", url="http://127.0.0.1:1/never")
    probe = await HttpProber(connect_timeout_s=2).probe(cfg, {})
    assert probe.status == "failed"
    assert probe.error_category == "SOURCE_UNREACHABLE"


def test_localhost_reachable_smoke() -> None:
    # keeps the module importable even if every container skips
    assert socket.AF_INET is not None
