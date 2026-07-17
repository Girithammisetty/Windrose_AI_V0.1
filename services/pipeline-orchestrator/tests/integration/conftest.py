"""Integration fixtures: real Postgres (Testcontainers) + alembic migrations +
non-privileged RLS role, plus reachability helpers for the running compose infra
(MLflow :5500, Redpanda :9092, MinIO :9000, OPA :8281). Auto-skips with a clear
message when Docker / a dependency is unavailable."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import FakeClock

ROOT = Path(__file__).resolve().parents[2]

APP_USER = "pipeline_rt"
APP_PASSWORD = "rt-secret"

MLFLOW_URI = os.environ.get("PPL_MLFLOW_URI", "http://localhost:5500")
KAFKA = os.environ.get("PPL_KAFKA", "localhost:9092")
REDIS_URL = os.environ.get("PPL_REDIS", "redis://localhost:6379/0")
S3_ENDPOINT = os.environ.get("PPL_S3", "http://localhost:9000")
OPA_URL = os.environ.get("PPL_OPA", "http://localhost:8281")
ARTIFACTS_BUCKET = "windrose-pipelines"

TABLES = ["pipeline_runs", "pipeline_template_versions", "pipeline_templates",
          "tenant_quotas", "run_queue", "labeled_examples", "outbox",
          "idempotency_keys", "processed_events"]


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def mlflow_up() -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(f"{MLFLOW_URI}/health", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


def kafka_up() -> bool:
    host, _, port = KAFKA.partition(":")
    return _port_open(host, int(port or 9092))


def ensure_bucket() -> bool:
    try:
        import boto3
        from botocore.client import Config as BotoConfig

        client = boto3.client(
            "s3", endpoint_url=S3_ENDPOINT, aws_access_key_id="windrose",
            aws_secret_access_key="windrose_dev", region_name="us-east-1",
            config=BotoConfig(signature_version="s3v4",
                              s3={"addressing_style": "path"}))
        existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
        if ARTIFACTS_BUCKET not in existing:
            client.create_bucket(Bucket=ARTIFACTS_BUCKET)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def pg():
    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker unavailable — skipping integration tier: {exc}")
        return

    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    creds = (container.username, container.password, container.dbname)
    super_url = f"postgresql://{creds[0]}:{creds[1]}@{host}:{port}/{creds[2]}"

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["PPL_MIGRATE_URL"] = super_url.replace("postgresql://",
                                                      "postgresql+psycopg://")
    command.upgrade(cfg, "head")

    import psycopg

    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE USER {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' IN ROLE pipeline_app")

    yield {
        "super_url": super_url,
        "app_url": f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{creds[2]}",
        # The SHIPPED default runtime role (config.py database_url uses pipeline_app),
        # created by the migration as a non-owner, non-superuser DML role.
        "default_url": f"postgresql+asyncpg://pipeline_app:pipeline_app@{host}:{port}/{creds[2]}",
        "db": creds[2],
    }
    container.stop()


@pytest.fixture
def _clean_db(pg):
    import psycopg

    with psycopg.connect(pg["super_url"], autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
    yield


@pytest.fixture
async def app_engine(pg, _clean_db):
    engine = create_async_engine(pg["app_url"], pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
def app_sf(app_engine):
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
def clock():
    return FakeClock()
