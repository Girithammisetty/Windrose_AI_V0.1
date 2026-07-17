"""Integration fixtures: real Postgres (pgvector image, Testcontainers) +
alembic migrations + non-privileged RLS role, plus real Redis. Auto-skips with
a clear message when Docker is unavailable."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.container import build_container
from app.main import create_app
from tests.conftest import FakeClock, make_settings

ROOT = Path(__file__).resolve().parents[2]

APP_USER = "ai_gateway_rt"
APP_PASSWORD = "rt-secret"

TABLES = [
    "request_log", "semantic_cache_entries", "budget_reservations",
    "budget_threshold_flags", "budget_spend", "budgets", "virtual_keys",
    "guardrail_policies", "model_ladders", "provider_deployments",
    "tenant_configs", "outbox", "idempotency_keys", "processed_events",
]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def pg():
    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("pgvector/pgvector:pg16")
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
    os.environ["AIG_MIGRATE_URL"] = super_url.replace("postgresql://",
                                                      "postgresql+psycopg://")
    command.upgrade(cfg, "head")

    # Non-superuser application login — RLS applies to it (MASTER-FR-001)
    import psycopg

    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE USER {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' "
            f"IN ROLE ai_gateway_app"
        )

    yield {
        "super_url": super_url,
        "app_url": (
            f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{creds[2]}"
        ),
    }
    container.stop()


@pytest.fixture(scope="session")
def redis_url(pg):
    try:
        from testcontainers.redis import RedisContainer

        container = RedisContainer("redis:7-alpine")
        container.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker unavailable — skipping integration tier: {exc}")
        return
    host = container.get_container_host_ip()
    port = container.get_exposed_port(6379)
    yield f"redis://{host}:{port}/0"
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
async def redis_client(redis_url):
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def clock():
    return FakeClock()


async def _noop_sleeper(ms: int) -> None:
    return None


@pytest.fixture
async def container(engine, redis_client, clock):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return build_container(
        make_settings(), mode="sql", session_factory=session_factory,
        redis=redis_client, clock=clock, sleeper=_noop_sleeper,
    )


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
