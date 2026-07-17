"""Live integration tests for the credential-gated cloud/SaaS drivers.

Each test runs the REAL adapter against the REAL service, but only when the
connector's credentials are present in the environment; otherwise it
skips-with-reason naming exactly what is needed (see README "Going live"). The
offline request/response shaping is covered by
``tests/unit/test_cloud_driver_contracts.py``.

Spanner additionally has a genuinely-local path: the Cloud Spanner **emulator**
container. That test runs for real when the ``cloud`` extra is installed and
Docker is available (no GCP account needed).
"""

from __future__ import annotations

import os

import pytest

from app.domain.connectors import (
    BigqueryConfig,
    DatabricksConfig,
    RedshiftConfig,
    SalesforceConfig,
    SnowflakeConfig,
    SpannerConfig,
    SynapseConfig,
)
from app.domain.drivers.bigquery import BigQueryProber, BigQueryQuerySource
from app.domain.drivers.databricks import databricks_dialect
from app.domain.drivers.dbapi import DbapiProber
from app.domain.drivers.mssql import SqlServerProber
from app.domain.drivers.redshift import redshift_dialect
from app.domain.drivers.salesforce import SalesforceProber, SalesforceQuerySource
from app.domain.drivers.snowflake import snowflake_dialect
from app.domain.drivers.spanner import SpannerQuerySource

pytestmark = pytest.mark.integration


def _require(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        pytest.skip(f"needs credentials: set {', '.join(missing)} to run this live test")
    return {n: os.environ[n] for n in names}


async def test_snowflake_live_probe() -> None:
    env = _require(
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
    )
    cfg = SnowflakeConfig(
        account=env["SNOWFLAKE_ACCOUNT"],
        username=env["SNOWFLAKE_USER"],
        warehouse=env["SNOWFLAKE_WAREHOUSE"],
        database=env["SNOWFLAKE_DATABASE"],
    )
    result = await DbapiProber(snowflake_dialect()).probe(
        cfg, {"password": env["SNOWFLAKE_PASSWORD"]}
    )
    assert result.status == "ok", result.error_detail


async def test_redshift_live_probe() -> None:
    env = _require("REDSHIFT_HOST", "REDSHIFT_DATABASE", "REDSHIFT_USER", "REDSHIFT_PASSWORD")
    cfg = RedshiftConfig(
        host=env["REDSHIFT_HOST"], database=env["REDSHIFT_DATABASE"], username=env["REDSHIFT_USER"]
    )
    result = await DbapiProber(redshift_dialect()).probe(
        cfg, {"password": env["REDSHIFT_PASSWORD"]}
    )
    assert result.status == "ok", result.error_detail


async def test_databricks_live_probe() -> None:
    env = _require("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN")
    cfg = DatabricksConfig(
        server_hostname=env["DATABRICKS_SERVER_HOSTNAME"], http_path=env["DATABRICKS_HTTP_PATH"]
    )
    result = await DbapiProber(databricks_dialect()).probe(
        cfg, {"access_token": env["DATABRICKS_TOKEN"]}
    )
    assert result.status == "ok", result.error_detail


async def test_bigquery_live_probe() -> None:
    env = _require("BIGQUERY_PROJECT_ID", "BIGQUERY_CREDENTIALS_JSON")
    cfg = BigqueryConfig(project_id=env["BIGQUERY_PROJECT_ID"], dataset="_probe")
    result = await BigQueryProber().probe(
        cfg, {"credentials_json": env["BIGQUERY_CREDENTIALS_JSON"]}
    )
    assert result.status == "ok", result.error_detail


async def test_synapse_live_probe() -> None:
    env = _require("SYNAPSE_HOST", "SYNAPSE_DATABASE", "SYNAPSE_USER", "SYNAPSE_PASSWORD")
    cfg = SynapseConfig(
        host=env["SYNAPSE_HOST"], database=env["SYNAPSE_DATABASE"], username=env["SYNAPSE_USER"]
    )
    # Synapse exposes a TDS endpoint, so it is driven by the SQL Server driver.
    result = await SqlServerProber().probe(cfg, {"password": env["SYNAPSE_PASSWORD"]})
    assert result.status == "ok", result.error_detail


async def test_salesforce_live_probe_and_pull() -> None:
    env = _require(
        "SF_USERNAME",
        "SF_PASSWORD",
        "SF_SECURITY_TOKEN",
        "SF_CLIENT_ID",
        "SF_CLIENT_SECRET",
    )
    cfg = SalesforceConfig(username=env["SF_USERNAME"], domain=os.getenv("SF_DOMAIN", "login"))
    secrets = {
        "password": env["SF_PASSWORD"],
        "security_token": env["SF_SECURITY_TOKEN"],
        "client_id": env["SF_CLIENT_ID"],
        "client_secret": env["SF_CLIENT_SECRET"],
    }
    probe = await SalesforceProber().probe(cfg, secrets)
    assert probe.status == "ok", probe.error_detail

    source = SalesforceQuerySource()
    rows = []
    async for batch in source.execute(
        cfg, secrets, "SELECT Id, Name FROM Account LIMIT 5", {}, 100
    ):
        rows.extend(batch)
    assert all("Id" in r for r in rows)


async def test_bigquery_live_pull() -> None:
    env = _require("BIGQUERY_PROJECT_ID", "BIGQUERY_CREDENTIALS_JSON", "BIGQUERY_TEST_SQL")
    cfg = BigqueryConfig(project_id=env["BIGQUERY_PROJECT_ID"], dataset="_probe")
    source = BigQueryQuerySource()
    rows = []
    async for batch in source.execute(
        cfg,
        {"credentials_json": env["BIGQUERY_CREDENTIALS_JSON"]},
        env["BIGQUERY_TEST_SQL"],
        {},
        100,
    ):
        rows.extend(batch)
    assert rows


# ===================================================================== Spanner emulator (real)


@pytest.fixture(scope="session")
def spanner_emulator():
    """Real Cloud Spanner emulator: instance + database + one row seeded.

    Runs when the ``cloud`` extra is installed and Docker is available — no GCP
    account required. Skips clearly otherwise.
    """
    try:
        from google.cloud import spanner  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"google-cloud-spanner not installed (install the 'cloud' extra): {exc}")
    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers not installed: {exc}")

    try:
        container = DockerContainer(
            "gcr.io/cloud-spanner-emulator/emulator:latest"
        ).with_exposed_ports(9010)
        container.start()
        wait_for_logs(container, "gRPC server listening", timeout=60)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Docker/Spanner emulator unavailable: {exc}")

    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(9010))
    os.environ["SPANNER_EMULATOR_HOST"] = f"{host}:{port}"

    project, instance_id, database_id = "test-project", "test-instance", "test-db"
    try:
        from google.api_core import exceptions as gexc
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import spanner

        client = spanner.Client(project=project, credentials=AnonymousCredentials())
        config_name = f"{client.project_name}/instanceConfigs/emulator-config"
        instance = client.instance(instance_id, configuration_name=config_name, node_count=1)
        try:
            instance.create().result(60)
        except gexc.AlreadyExists:
            pass
        database = instance.database(
            database_id,
            ddl_statements=[
                "CREATE TABLE orders (id INT64, name STRING(64), updated_at TIMESTAMP) "
                "PRIMARY KEY (id)"
            ],
        )
        database.create().result(60)
        with database.batch() as batch:
            batch.insert(
                table="orders",
                columns=("id", "name", "updated_at"),
                values=[
                    (1, "alpha", "2026-06-30T00:00:00Z"),
                    (2, "beta", "2026-07-02T00:00:00Z"),
                    (3, "gamma", "2026-07-05T00:00:00Z"),
                ],
            )
    except Exception as exc:  # pragma: no cover
        container.stop()
        os.environ.pop("SPANNER_EMULATOR_HOST", None)
        pytest.skip(f"Spanner emulator setup failed: {exc}")

    yield {"project_id": project, "instance_id": instance_id, "database": database_id}
    container.stop()
    os.environ.pop("SPANNER_EMULATOR_HOST", None)


async def test_spanner_emulator_query_pull_with_watermark(spanner_emulator) -> None:
    from datetime import UTC, datetime

    from app.domain.watermark import WatermarkSpec, build_incremental_query

    cfg = SpannerConfig(**spanner_emulator)
    source = SpannerQuerySource(connect_timeout_s=30, query_timeout_s=60)

    sql, params = build_incremental_query(
        "SELECT id, name, updated_at FROM orders",
        WatermarkSpec(
            column="updated_at",
            operator=">",
            value_type="timestamp",
            value="2026-07-01T00:00:00Z",
        ),
    )
    assert sql.endswith("WHERE updated_at > :watermark")  # placeholder, not spliced
    assert params["watermark"] == datetime(2026, 7, 1, tzinfo=UTC)

    rows = []
    async for batch in source.execute(cfg, {}, sql, params, 10):
        rows.extend(batch)
    # only the two rows past the watermark, bound as a typed @watermark param
    assert {r["name"] for r in rows} == {"beta", "gamma"}
