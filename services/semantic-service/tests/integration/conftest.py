"""Integration fixtures: real Postgres (Testcontainers, pgvector image) +
alembic migrations + non-privileged RLS role. Auto-skips with a clear message
when Docker is unavailable."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, FakeClock, make_settings, seed_datasets

ROOT = Path(__file__).resolve().parents[2]

APP_USER = "semantic_rt"
APP_PASSWORD = "rt-secret"

TABLES = [
    "compile_log", "operations", "chart_refs", "verified_queries",
    "entities", "dimensions", "measures", "join_paths",
    "outbox", "idempotency_keys", "processed_events",
    "model_versions", "semantic_models",
]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def pg():
    try:
        from testcontainers.postgres import PostgresContainer

        # pgvector-enabled image (verified_queries.embedding vector(1024))
        container = PostgresContainer("pgvector/pgvector:pg16")
        container.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker unavailable — skipping integration tier: {exc}")
        return

    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    creds = (container.username, container.password, container.dbname)
    super_url = f"postgresql://{creds[0]}:{creds[1]}@{host}:{port}/{creds[2]}"

    # Migrations as the privileged role (CI applies them the same way)
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["SEM_MIGRATE_URL"] = super_url.replace(
        "postgresql://", "postgresql+psycopg://")
    command.upgrade(cfg, "head")

    # Non-superuser application login — RLS applies to it (MASTER-FR-001)
    import psycopg

    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE USER {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' "
            f"IN ROLE semantic_app"
        )

    yield {
        "super_url": super_url,
        "app_url": (
            f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{creds[2]}"
        ),
    }
    container.stop()


@pytest.fixture
def _clean_db(pg):
    import psycopg

    with psycopg.connect(pg["super_url"], autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
    yield


@pytest.fixture
async def engine(pg, _clean_db):
    engine = create_async_engine(pg["app_url"], pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
async def container(engine, clock):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    c = build_container(make_settings(), mode="sql",
                        session_factory=session_factory, clock=clock)
    seed_datasets(c.dataset_client, TENANT_A)
    seed_datasets(c.dataset_client, TENANT_B)
    return c


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
