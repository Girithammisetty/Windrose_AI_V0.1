"""Integration: Postgres RLS tenant-isolation (MASTER-FR-001/004, AC-13)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.entities import InferenceJob
from app.store.sql import sql_uow_factory
from app.utils import utcnow, uuid7

pytestmark = pytest.mark.integration

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"


def _job(tenant: str, name: str) -> InferenceJob:
    now = utcnow()
    return InferenceJob(
        id=str(uuid7()), tenant_id=tenant, workspace_id=WORKSPACE, name=name, status=6,
        model_version_urn=f"wr:{tenant}:experiment:model_version/m@1",
        input_dataset_urn=f"wr:{tenant}:dataset:dataset/d", submitted_by="u1",
        created_at=now, updated_at=now)


@pytest.fixture
def uow_factory(engine):
    return sql_uow_factory(async_sessionmaker(engine, expire_on_commit=False))


async def test_cross_tenant_job_invisible(uow_factory):
    async with uow_factory(TENANT_A) as uow:
        job = _job(TENANT_A, "a-job")
        await uow.jobs.add(job)
    # tenant B cannot see tenant A's job
    async with uow_factory(TENANT_B) as uow:
        assert await uow.jobs.get(job.id) is None
    # tenant A can
    async with uow_factory(TENANT_A) as uow:
        assert (await uow.jobs.get(job.id)) is not None


async def test_rls_enforced_at_sql_level(uow_factory, engine):
    async with uow_factory(TENANT_A) as uow:
        await uow.jobs.add(_job(TENANT_A, "sql-job"))
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM inference_jobs"))).scalar()
        assert count == 0  # no tenant context -> nothing visible
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_B})
        assert (await conn.execute(text("SELECT count(*) FROM inference_jobs"))).scalar() == 0
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
        assert (await conn.execute(text("SELECT count(*) FROM inference_jobs"))).scalar() == 1


async def test_rls_insert_check_blocks_wrong_tenant(engine):
    from sqlalchemy.exc import DBAPIError

    async with engine.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
        with pytest.raises(DBAPIError):
            await conn.execute(
                text(
                    "INSERT INTO inference_jobs (id, tenant_id, workspace_id, name, status, "
                    "model_version_urn, input_dataset_urn, submitted_by, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :tb, gen_random_uuid(), 'evil', 0, 'm', 'd', "
                    "'x', now(), now())"
                ),
                {"tb": TENANT_B},
            )


async def test_shipped_default_dsn_role_is_non_superuser_and_forced():
    """FINDING-1 guard: the SHIPPED default config connects as inference_app, a
    non-superuser/non-owner role, and RLS is FORCEd on tenant tables."""
    from app.config import Settings

    dsn = Settings().database_url
    assert "inference_app" in dsn
    assert "windrose:windrose_dev" not in dsn  # never the dev superuser


async def test_shipped_default_dsn_enforces_rls(pg):
    """Boot a real engine with the DEFAULT runtime DSN (inference_app role, not a
    test-only role) and prove cross-tenant access returns nothing — the shipped
    default is safe, not just the test harness (AC-13 / FINDING-1)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(pg["default_url"], pool_pre_ping=True)
    try:
        uow_factory = sql_uow_factory(async_sessionmaker(engine, expire_on_commit=False))
        # the inference_app role is non-superuser and cannot bypass RLS
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"))).one()
            assert row.rolsuper is False
            assert row.rolbypassrls is False
            forced = (await conn.execute(text(
                "SELECT relforcerowsecurity FROM pg_class "
                "WHERE relname = 'inference_jobs'"))).scalar()
            assert forced is True
        # write a tenant-A job through the restricted role
        async with uow_factory(TENANT_A) as uow:
            job = _job(TENANT_A, "default-dsn")
            await uow.jobs.add(job)
        # tenant B (same restricted role) sees nothing; no context sees nothing
        async with uow_factory(TENANT_B) as uow:
            assert await uow.jobs.get(job.id) is None
        async with engine.connect() as conn:
            assert (await conn.execute(
                text("SELECT count(*) FROM inference_jobs"))).scalar() == 0
        async with uow_factory(TENANT_A) as uow:
            assert await uow.jobs.get(job.id) is not None
    finally:
        await engine.dispose()


async def test_schedule_cross_tenant_isolated(uow_factory):
    from app.domain.entities import ScoringSchedule

    now = utcnow()
    sch = ScoringSchedule(
        id=str(uuid7()), tenant_id=TENANT_A, workspace_id=WORKSPACE, name="s1",
        input_selector={"dataset_urn": "x"}, output={"mode": "append"}, created_by="u1",
        created_at=now, updated_at=now, model_version_urn="m", cron="0 3 * * *")
    async with uow_factory(TENANT_A) as uow:
        await uow.schedules.add(sch)
    async with uow_factory(TENANT_B) as uow:
        assert await uow.schedules.get(sch.id) is None


def _sched(tenant: str, name: str):
    from app.domain.entities import ScoringSchedule

    now = utcnow()
    return ScoringSchedule(
        id=str(uuid7()), tenant_id=tenant, workspace_id=WORKSPACE, name=name,
        input_selector={"dataset_urn": "x"}, output={"mode": "append"}, created_by="u1",
        created_at=now, updated_at=now, model_version_urn="m", interval_seconds=60,
        next_fire_at=now)


async def test_worker_session_reads_across_tenants(uow_factory):
    """The scheduler/reaper worker session (worker=True) must read across tenants
    without the RLS tenant policy erroring on a bogus tenant GUC (regression:
    'invalid input syntax for type uuid: "*"' at boot)."""
    async with uow_factory(TENANT_A) as uow:
        await uow.schedules.add(_sched(TENANT_A, "wa"))
    async with uow_factory(TENANT_B) as uow:
        await uow.schedules.add(_sched(TENANT_B, "wb"))
    async with uow_factory("*", worker=True) as uow:
        all_enabled = await uow.schedules.all_enabled()
        # worker sees both tenants' schedules (cross-tenant read, no cast error)
        names = {s.name for s in all_enabled}
    assert {"wa", "wb"} <= names
