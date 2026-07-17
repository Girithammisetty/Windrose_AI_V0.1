"""FINDING-1: prove the SHIPPED DEFAULT is safe — the role in the default
EXP_DATABASE_URL must be non-superuser, non-owner, and RLS (FORCEd) must hide
other tenants' rows for it. This does not use any test-only role: it connects as
exactly the user embedded in Settings().database_url."""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from tests.conftest import TENANT_A, TENANT_B, ctx_for, make_experiment

pytestmark = pytest.mark.integration


def _default_dsn_user_password() -> tuple[str, str]:
    parts = urlsplit(Settings().database_url)
    return parts.username, parts.password


async def test_default_dsn_role_is_not_superuser_or_owner(pg):
    user, _ = _default_dsn_user_password()
    assert user == "experiment_app"
    import psycopg

    with psycopg.connect(pg["super_url"], autocommit=True) as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (user,)
        ).fetchone()
        assert row is not None, "shipped default role missing"
        assert row[0] is False, "default runtime role must NOT be a superuser"
        assert row[1] is False, "default runtime role must NOT bypass RLS"
        # not the table owner either
        owner = conn.execute(
            "SELECT tableowner FROM pg_tables WHERE tablename = 'experiments'").fetchone()[0]
        assert owner != user, "default runtime role must not own the tables"


async def test_default_dsn_enforces_tenant_isolation(pg, container):
    # seed tenant A's data through the service (also running as experiment_app)
    ctx = ctx_for(TENANT_A)
    exp = await make_experiment(container, ctx, name=f"dsn-{uuid.uuid4().hex[:8]}")

    # connect with EXACTLY the default DSN's credentials (host/port/db from the
    # ephemeral container) — the shipped runtime identity, nothing test-only.
    user, pwd = _default_dsn_user_password()
    dsn = f"postgresql+asyncpg://{user}:{pwd}@{pg['host']}:{pg['port']}/{pg['db']}"
    engine = create_async_engine(dsn)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as s:
            # tenant B context: row invisible (FORCE RLS applies to this role)
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                            {"t": TENANT_B})
            assert (await s.execute(
                text("SELECT count(*) FROM experiments WHERE id = :i"), {"i": exp.id}
            )).scalar() == 0
            # no tenant context at all: still invisible
            await s.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            assert (await s.execute(
                text("SELECT count(*) FROM experiments"))).scalar() == 0
            # tenant A context: visible
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                            {"t": TENANT_A})
            assert (await s.execute(
                text("SELECT count(*) FROM experiments WHERE id = :i"), {"i": exp.id}
            )).scalar() == 1
    finally:
        await engine.dispose()
