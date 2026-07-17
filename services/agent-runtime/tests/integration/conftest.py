"""Integration fixtures: real Postgres (Testcontainers pgvector) + alembic +
non-privileged RLS role, and reachability probes for live infra (Temporal, Kafka,
OPA, ai-gateway, tool-plane, case-service, Ollama). Each auto-skips with a clear
message when its dependency is unavailable (CONVENTIONS test tiers)."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]

APP_USER = "agent_runtime_rt"
APP_PASSWORD = "rt-secret"

TEMPORAL_TARGET = os.environ.get("AR_TEMPORAL_TARGET", "localhost:7233")
KAFKA = os.environ.get("AR_KAFKA", "localhost:9092")
OPA_URL = os.environ.get("AR_OPA_URL", "http://localhost:8281")
AI_GATEWAY = os.environ.get("AR_AI_GATEWAY_URL", "http://localhost:8092")
TOOL_PLANE = os.environ.get("AR_TOOL_PLANE_URL", "http://localhost:8091")
CASE_SERVICE = os.environ.get("AR_CASE_SERVICE_URL", "http://localhost:8084")
OLLAMA = os.environ.get("AR_OLLAMA_URL", "http://localhost:11434")

pytestmark = pytest.mark.integration


def _port_open(hostport: str) -> bool:
    host, _, port = hostport.partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=2):
            return True
    except OSError:
        return False


def _http_ok(url: str, path: str = "/") -> bool:
    try:
        r = httpx.get(url.rstrip("/") + path, timeout=3)
        return r.status_code < 500
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def pg():
    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("pgvector/pgvector:pg16")
        container.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker/pgvector unavailable — skipping integration tier: {exc}")
        return

    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    user, pwd, db = container.username, container.password, container.dbname
    super_url = f"postgresql://{user}:{pwd}@{host}:{port}/{db}"

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["AR_MIGRATE_URL"] = super_url.replace("postgresql://", "postgresql+psycopg://")
    command.upgrade(cfg, "head")

    import psycopg

    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE USER {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' IN ROLE agent_runtime_app")

    yield {
        "super_url_async": super_url.replace("postgresql://", "postgresql+asyncpg://"),
        "app_url": f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{db}",
        "super_sync": super_url,
    }
    container.stop()


@pytest.fixture
async def app_session_factory(pg):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(pg["app_url"], pool_pre_ping=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def super_session_factory(pg):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(pg["super_url_async"], pool_pre_ping=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def require_temporal():
    if not _port_open(TEMPORAL_TARGET):
        pytest.skip(f"Temporal unreachable at {TEMPORAL_TARGET}")


@pytest.fixture
def require_kafka():
    if not _port_open(KAFKA):
        pytest.skip(f"Kafka/Redpanda unreachable at {KAFKA}")


@pytest.fixture
def require_ollama():
    if not _http_ok(OLLAMA, "/api/tags"):
        pytest.skip(f"Ollama unreachable at {OLLAMA}")


@pytest.fixture
def require_ai_gateway():
    if not _http_ok(AI_GATEWAY, "/healthz") and not _port_open(AI_GATEWAY.split("//")[-1]):
        pytest.skip(f"ai-gateway unreachable at {AI_GATEWAY}")


@pytest.fixture
def require_tool_plane():
    if not _port_open(TOOL_PLANE.split("//")[-1]):
        pytest.skip(f"tool-plane gateway unreachable at {TOOL_PLANE}")


@pytest.fixture
def require_case_service():
    if not _port_open(CASE_SERVICE.split("//")[-1]):
        pytest.skip(f"case-service unreachable at {CASE_SERVICE}")


@pytest.fixture
def require_opa():
    if not _http_ok(OPA_URL, "/health"):
        pytest.skip(f"OPA unreachable at {OPA_URL}")
