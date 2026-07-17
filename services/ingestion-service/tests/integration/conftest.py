"""Integration tier: real Postgres via Testcontainers, alembic migrations,
RLS enforced through a NON-superuser role (superusers bypass RLS).

Auto-skips with a clear message when Docker is unavailable (CONVENTIONS.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.container import build_container
from app.main import create_app
from tests.util import AUDIENCE, ISSUER

SERVICE_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "app_rls"
APP_ROLE_PW = "app_rls_pw"

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def pg_container():
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers not installed: {exc}")
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Docker unavailable — skipping integration tier: {exc}")
    yield container
    container.stop()


@pytest.fixture(scope="session")
def pg_urls(pg_container) -> tuple[str, str]:
    """(superuser sync url, app-role asyncpg url) with migrations applied."""
    su_url = pg_container.get_connection_url().replace("+psycopg2", "+psycopg")

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(SERVICE_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", su_url)
    command.upgrade(cfg, "head")

    engine = sa.create_engine(su_url)
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE_PW}'"))
        conn.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        conn.execute(
            sa.text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
            )
        )
    engine.dispose()

    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(5432)
    dbname = pg_container.dbname
    app_url = f"postgresql+asyncpg://{APP_ROLE}:{APP_ROLE_PW}@{host}:{port}/{dbname}"
    return su_url, app_url


@pytest.fixture
def su_engine(pg_urls):
    engine = sa.create_engine(pg_urls[0])
    yield engine
    engine.dispose()


@pytest.fixture
def _clean_db(su_engine):
    """Truncate everything between tests (superuser bypasses RLS)."""
    tables = (
        "upload_parts",
        "uploads",
        "ingestion_transitions",
        "webhook_event_dedup",
        "webhook_endpoints",
        "ingestions",
        "schedules",
        "connections",
        "outbox",
        "idempotency_keys",
    )
    with su_engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {', '.join(tables)} CASCADE"))
    yield


@pytest.fixture
async def pg_app_container(pg_urls, _clean_db, tmp_path, rsa_keys):
    _, public_pem = rsa_keys
    settings = Settings(
        database_url=pg_urls[1],
        environment="test",
        data_dir=str(tmp_path / "data"),
        jwt_public_key_pem=public_pem.decode(),
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        inline_execution=True,
        retry_backoff_base_s=0.0,
        progress_min_interval_s=0.0,
        min_part_size=256,
        default_part_size=1024,
    )
    container = build_container(settings)
    yield container
    await container.db.dispose()


@pytest.fixture
async def pg_client(pg_app_container):
    app = create_app(pg_app_container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://ingestion.test"
    ) as http:
        yield http
