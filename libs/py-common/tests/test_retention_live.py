"""B6/B7 (BRD 58): retention reaper against REAL Postgres.

The original test_retention.py used a fake session, which let a genuinely
broken statement ship: sqlalchemy.text()'s bind-param regex has a negative
lookahead for `:`, so the original `:retention_seconds::text` was silently NOT
treated as a bind param — the SQL reached the driver with a literal colon and
every service's hourly sweep failed (and was swallowed by logger.exception)
from the day B6/B7 landed. These tests run the real statement on the real
dev-infra Postgres (skipping gracefully when it isn't up), including the exact
FORCE-RLS shape processed_events has in every owner service, so the SQL, the
bind, the batching, AND the worker-GUC RLS behavior are all proven for real.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from datacern_common.retention import RetentionSpec, prune_table

ADMIN_DSN = "postgresql+asyncpg://datacern:datacern_dev@localhost:5432/datacern"

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
async def rls_table(postgres, unique):
    """A scratch table mirroring processed_events' exact RLS shape (FORCE RLS,
    tenant-isolation policy + app.worker cross-tenant policy) plus a scratch
    NON-superuser login role, so RLS genuinely applies to the pruning session
    (superusers bypass RLS even under FORCE)."""
    table = f"retention_live_{unique}"
    role = f"retention_role_{unique}"
    admin = create_async_engine(ADMIN_DSN, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(sa.text(f"""
            CREATE TABLE {table} (
                event_id uuid PRIMARY KEY,
                tenant_id uuid NOT NULL,
                created_at timestamptz NOT NULL,
                published_at timestamptz
            )"""))
        await conn.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        await conn.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        await conn.execute(sa.text(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"""))
        await conn.execute(sa.text(f"""
            CREATE POLICY worker ON {table}
            USING (coalesce(current_setting('app.worker', true), '') = 'true')"""))
        await conn.execute(sa.text(
            f"CREATE ROLE {role} LOGIN PASSWORD 'pw' NOSUPERUSER NOBYPASSRLS"))
        await conn.execute(sa.text(
            f"GRANT SELECT, INSERT, DELETE ON {table} TO {role}"))
        # Seed: two tenants with one aged row each + one fresh row for tenant A.
        await conn.execute(sa.text(f"""
            INSERT INTO {table} (event_id, tenant_id, created_at) VALUES
            (gen_random_uuid(), '{TENANT_A}', now() - interval '10 days'),
            (gen_random_uuid(), '{TENANT_A}', now()),
            (gen_random_uuid(), '{TENANT_B}', now() - interval '10 days')"""))
    worker_engine = create_async_engine(
        f"postgresql+asyncpg://{role}:pw@localhost:5432/datacern")
    try:
        yield table, async_sessionmaker(worker_engine, expire_on_commit=False), admin
    finally:
        await worker_engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
            await conn.execute(sa.text(f"DROP ROLE IF EXISTS {role}"))
        await admin.dispose()


async def _count(admin, table: str) -> int:
    async with admin.connect() as conn:
        return (await conn.execute(sa.text(f"SELECT count(*) FROM {table}"))).scalar_one()


async def test_prune_without_worker_guc_is_a_silent_rls_noop(rls_table):
    """The exact trap B6/B7's design doc warns about: no GUC -> RLS matches
    zero rows -> no error, nothing deleted."""
    table, sf, admin = rls_table
    spec = RetentionSpec(table=table, ts_col="created_at",
                         retention=timedelta(hours=48))
    assert await prune_table(sf, spec) == 0
    assert await _count(admin, table) == 3


async def test_prune_with_worker_guc_deletes_aged_rows_across_tenants(rls_table):
    """The production spec shape: worker GUC set -> both tenants' aged rows go,
    the fresh row stays, and a second sweep is an idempotent no-op."""
    table, sf, admin = rls_table
    spec = RetentionSpec(table=table, ts_col="created_at",
                         retention=timedelta(hours=48),
                         worker_guc="app.worker", worker_val="true")
    assert await prune_table(sf, spec) == 2
    assert await _count(admin, table) == 1
    assert await prune_table(sf, spec) == 0


async def test_outbox_shape_prunes_only_published_rows(rls_table):
    """require_not_null=True (the B6 outbox spec): an aged-but-unpublished row
    must survive regardless of age — only delivered events are safe to drop."""
    table, sf, admin = rls_table
    async with admin.connect() as conn:
        # Age + publish tenant A's fresh row; the two aged rows stay unpublished.
        await conn.execute(sa.text(f"""
            UPDATE {table} SET published_at = now() - interval '60 days',
                               created_at = now() - interval '60 days'
            WHERE tenant_id = '{TENANT_A}' AND published_at IS NULL
              AND created_at > now() - interval '1 day'"""))
    spec = RetentionSpec(table=table, ts_col="published_at",
                         retention=timedelta(days=30), require_not_null=True,
                         worker_guc="app.worker", worker_val="true")
    assert await prune_table(sf, spec) == 1
    assert await _count(admin, table) == 2  # the aged unpublished rows survive


async def test_batching_sweeps_until_drained(rls_table):
    """batch_size smaller than the doomed set -> multiple passes, all deleted."""
    table, sf, admin = rls_table
    async with admin.connect() as conn:
        await conn.execute(sa.text(f"""
            INSERT INTO {table} (event_id, tenant_id, created_at)
            SELECT gen_random_uuid(), '{TENANT_A}', now() - interval '10 days'
            FROM generate_series(1, 7)"""))
    spec = RetentionSpec(table=table, ts_col="created_at",
                         retention=timedelta(hours=48), batch_size=3,
                         worker_guc="app.worker", worker_val="true")
    assert await prune_table(sf, spec) == 9  # 7 new + the 2 seeded aged rows
    assert await _count(admin, table) == 1  # only the fresh row remains
