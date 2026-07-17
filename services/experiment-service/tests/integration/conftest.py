"""Integration fixtures: real Postgres (Testcontainers) + alembic migrations +
non-privileged RLS role, wired to the REAL MLflow tracking server. Reachability
probes for the live compose infra (MLflow, Redis, Kafka, OPA) auto-skip when a
dependency is down."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.mlflow_client import MlflowClient
from app.config import Settings
from app.container import build_container
from app.main import create_app
from app.utils import Clock
from tests.conftest import AUDIENCE, ISSUER, PUBLIC_PEM

ROOT = Path(__file__).resolve().parents[2]

# The shipped default runtime role (created by migration 0002). Integration
# tests run as THIS role — the same non-superuser, non-owner identity app.main
# uses by default — so RLS is exercised for the shipped default, not a
# test-only harness role (FINDING-1).
APP_USER = "experiment_app"
APP_PASSWORD = "experiment_app"

MLFLOW_URI = os.environ.get("EXP_MLFLOW_TRACKING_URI", "http://localhost:5500")
REDIS_URL = os.environ.get("EXP_REDIS_URL", "redis://localhost:6379/0")
KAFKA = os.environ.get("EXP_KAFKA", "localhost:9092")
OPA_URL = os.environ.get("EXP_OPA_URL", "http://localhost:8281")

pytestmark = pytest.mark.integration

TABLES = [
    "model_cards", "model_registration_log", "promotions", "model_versions",
    "registered_models", "run_metric_history", "run_metrics", "run_params",
    "run_tags", "run_artifacts", "run_notes", "runs", "experiments",
    "mirror_inbox", "reconciliation_watermarks", "outbox", "idempotency_keys",
    "processed_events",
]


def reachable(port: int, host: str = "localhost") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def require_port(port: int, name: str) -> None:
    if not reachable(port):
        pytest.skip(f"{name} not reachable on localhost:{port} — dev infra down")


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
    user, pwd, db = container.username, container.password, container.dbname
    super_url = f"postgresql://{user}:{pwd}@{host}:{port}/{db}"

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["EXP_MIGRATE_URL"] = super_url.replace("postgresql://", "postgresql+psycopg://")
    # Migrations run as the privileged (superuser) role; 0002 creates the
    # non-privileged experiment_app LOGIN role the runtime + tests then use.
    command.upgrade(cfg, "head")

    yield {
        "super_url": super_url,
        "super_async": super_url.replace("postgresql://", "postgresql+asyncpg://"),
        "app_url": f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{db}",
        "host": host, "port": port, "db": db,
    }
    container.stop()


@pytest.fixture
def _clean(pg):
    import psycopg

    with psycopg.connect(pg["super_url"], autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
    yield


@pytest.fixture
async def engine(pg, _clean):
    engine = create_async_engine(pg["app_url"], pool_pre_ping=True)
    yield engine
    await engine.dispose()


def _settings() -> Settings:
    return Settings(use_real_adapters=False, jwt_public_key_pem=PUBLIC_PEM,
                    jwt_issuer=ISSUER, jwt_audience=AUDIENCE,
                    mlflow_tracking_uri=MLFLOW_URI, kafka_bootstrap_servers=KAFKA,
                    redis_url=REDIS_URL, opa_url=OPA_URL)


@pytest.fixture
def real_mlflow():
    require_port(5500, "MLflow")
    return MlflowClient(MLFLOW_URI)


@pytest.fixture
async def container(engine, real_mlflow):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    c = build_container(_settings(), mode="sql", session_factory=session_factory,
                        mlflow=real_mlflow, clock=Clock())
    return c


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def mlflow_up() -> bool:
    try:
        return httpx.post(f"{MLFLOW_URI}/api/2.0/mlflow/experiments/search",
                          json={"max_results": 1}, timeout=3).status_code == 200
    except Exception:  # noqa: BLE001
        return False
