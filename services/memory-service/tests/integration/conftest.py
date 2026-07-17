"""Integration fixtures: real Postgres (Testcontainers pgvector) + alembic
migrations + non-privileged RLS role, plus reachability probes for the live
compose infra (Redis, Redpanda/Kafka, OPA) and Ollama embeddings. Each fixture
auto-skips with a clear message when its dependency is unavailable."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, FakeClock, make_settings

ROOT = Path(__file__).resolve().parents[2]

APP_USER = "memory_rt"
APP_PASSWORD = "rt-secret"

OLLAMA_URL = os.environ.get("MEM_EMBEDDINGS_BASE_URL", "http://localhost:11434/v1")
REDIS_URL = os.environ.get("MEM_REDIS_URL", "redis://localhost:6379/0")
KAFKA = os.environ.get("MEM_KAFKA", "localhost:9092")
OPA_URL = os.environ.get("MEM_OPA_URL", "http://localhost:8281")

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
    user, pwd, db = container.username, container.password, container.dbname
    super_url = f"postgresql://{user}:{pwd}@{host}:{port}/{db}"

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["MEM_MIGRATE_URL"] = super_url.replace(
        "postgresql://", "postgresql+psycopg://")
    command.upgrade(cfg, "head")

    import psycopg

    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE USER {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' IN ROLE memory_app")
        # provision the two test tenants (control-plane, privileged)
        for t in (TENANT_A, TENANT_B):
            conn.execute("SELECT mem_provision_tenant(%s)", (t,))

    yield {
        "super_url_async": super_url.replace("postgresql://", "postgresql+asyncpg://"),
        "app_url": f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{db}",
        "super_sync": super_url,
    }
    container.stop()


@pytest.fixture
def _clean(pg):
    import psycopg

    with psycopg.connect(pg["super_sync"], autocommit=True) as conn:
        for t in (TENANT_A, TENANT_B):
            sch = "mem_t_" + t.replace("-", "")
            conn.execute(f'TRUNCATE {sch}.memories, {sch}.rag_chunks')
        conn.execute("TRUNCATE corpora, tenant_policies, erasure_requests, "
                     "write_audit, outbox, processed_events, idempotency_keys")
    yield


@pytest.fixture
async def app_engine(pg, _clean):
    engine = create_async_engine(pg["app_url"], pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def admin_engine(pg, _clean):
    engine = create_async_engine(pg["super_url_async"], pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
def clock():
    return FakeClock()


def _reachable_ollama() -> bool:
    try:
        r = httpx.post(f"{OLLAMA_URL}/embeddings",
                       json={"model": "nomic-embed-text", "input": "ping"}, timeout=10)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def real_embedder():
    if not _reachable_ollama():
        pytest.skip(f"Ollama embeddings unreachable at {OLLAMA_URL}")
    from app.adapters.embeddings import OpenAIEmbeddingClient
    return OpenAIEmbeddingClient(OLLAMA_URL, model="nomic-embed-text")


@pytest.fixture
async def container(app_engine, admin_engine, clock):
    """SQL-mode container with the in-process (hash) embedder — DB-only tests."""
    app_sf = async_sessionmaker(app_engine, expire_on_commit=False)
    admin_sf = async_sessionmaker(admin_engine, expire_on_commit=False)
    c = build_container(make_settings(), mode="sql", session_factory=app_sf,
                        admin_session_factory=admin_sf, clock=clock)
    return c


@pytest.fixture
async def real_container(app_engine, admin_engine, clock, real_embedder):
    """SQL-mode container wired to the REAL Ollama embedder."""
    app_sf = async_sessionmaker(app_engine, expire_on_commit=False)
    admin_sf = async_sessionmaker(admin_engine, expire_on_commit=False)
    c = build_container(make_settings(), mode="sql", session_factory=app_sf,
                        admin_session_factory=admin_sf, clock=clock,
                        embedder=real_embedder)
    return c


@pytest.fixture
async def client(container):
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
