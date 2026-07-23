"""RLS policies must survive a pooled connection whose transaction-local
``app.tenant_id`` GUC has reverted to an empty string (BRD 58 SEC-4).

0005 fixed this for the original tenant tables via the NULLIF()-guarded cast;
0006/0007/0012 later added five new tenant tables (agent_transcripts,
sft_datasets, sft_examples, slm_training_jobs, slm_adapters) using the plain
cast again, reintroducing the same "invalid input syntax for type uuid: ''"
crash on connection reuse. Exercises two representative tables — the fix is
identical SQL applied uniformly across all five (migration 0018)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import TENANT_A

pytestmark = pytest.mark.integration


async def _seed_transcript(pg, tenant: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine as _mk

    engine = _mk(pg["super_url_async"])
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO agent_transcripts
                (transcript_id, tenant_id, run_id, agent_key, agent_version, principal_type)
            VALUES (:tid, :tenant, :run, 'case-triage', 1, 'user_obo')
        """), {"tid": str(uuid.uuid4()), "tenant": tenant, "run": str(uuid.uuid4())})
    await engine.dispose()


async def test_reused_connection_survives_guc_revert(pg):
    """A single pooled connection: set the GUC transaction-locally, let the
    transaction end (GUC reverts to '' at session level), then reuse the SAME
    connection for another query with no GUC set. Before migration 0018 this
    raises `invalid input syntax for type uuid: ""` on agent_transcripts —
    exactly 0005's bug, just on a table 0005 predates."""
    await _seed_transcript(pg, TENANT_A)

    # pool_size=1 + max_overflow=0 forces every checkout to be the same
    # physical connection, reproducing the pooled-reuse scenario for real.
    engine = create_async_engine(pg["app_url"], pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                             {"t": TENANT_A})
            await s.execute(text("SELECT 1"))
            await s.commit()  # transaction ends -> GUC reverts to '' at session level

        # Same pool, same underlying connection, no GUC set this time.
        async with factory() as s:
            res = await s.execute(text("SELECT count(*) c FROM agent_transcripts"))
            rows = res.mappings().first()
            assert rows["c"] == 0  # fail-closed: NULLIF(...)::uuid is NULL, not an error
    finally:
        await engine.dispose()


async def test_slm_training_jobs_policy_uses_nullif_form(super_session_factory):
    """Direct proof the on-disk policy expression is the safe NULLIF() form,
    not just that behavior happens to work — reads pg_policies for the table
    0012 introduced with the regressed plain-cast form."""
    async with super_session_factory() as s:
        row = (await s.execute(text(
            "SELECT qual FROM pg_policies "
            "WHERE tablename = 'slm_training_jobs' AND policyname = 'slm_training_jobs_isolation'"
        ))).mappings().first()
        assert row is not None
        assert "NULLIF" in row["qual"]
