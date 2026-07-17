"""Integration fixtures: real Postgres (Testcontainers) + alembic migrations +
non-privileged RLS role (eval_app). Auto-skips with a clear message when Docker
is unavailable, per CONVENTIONS.md."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.container import build_container
from app.main import create_app
from tests.conftest import FakeClock, FakeJudgeClient, make_settings

ROOT = Path(__file__).resolve().parents[2]

APP_USER = "eval_rt"
APP_PASSWORD = "rt-secret"

TABLES = [
    "case_results",
    "gate_results",
    "canary_comparisons",
    "slo_rollups",
    "eval_runs",
    "suites",
    "scorers",
    "eval_cases",
    "datasets",
    "outbox",
    "processed_events",
]


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
    os.environ["EVAL_MIGRATE_URL"] = super_url.replace("postgresql://", "postgresql+psycopg://")
    command.upgrade(cfg, "head")

    import psycopg

    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(f"CREATE USER {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' IN ROLE eval_app")

    yield {
        "super_url": super_url,
        "app_url": f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{creds[2]}",
        # The SHIPPED-DEFAULT runtime role (created by the migration): non-owner,
        # non-superuser, member of eval_app. Used to prove FORCE-RLS isolation with
        # the exact role the default DSN uses (not the test-only eval_rt role).
        "default_url": f"postgresql+asyncpg://eval_app_rt:eval_app_dev@{host}:{port}/{creds[2]}",
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
async def container(engine, tmp_path, clock):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return build_container(
        make_settings(tmp_path),
        mode="sql",
        session_factory=session_factory,
        clock=clock,
        judge_client=FakeJudgeClient(),
    )


@pytest.fixture
async def default_engine(pg, _clean_db):
    """Engine bound to the SHIPPED-DEFAULT non-owner role (eval_app_rt)."""
    engine = create_async_engine(pg["default_url"], pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def default_container(default_engine, tmp_path, clock):
    session_factory = async_sessionmaker(default_engine, expire_on_commit=False)
    return build_container(
        make_settings(tmp_path), mode="sql", session_factory=session_factory,
        clock=clock, judge_client=FakeJudgeClient())


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
