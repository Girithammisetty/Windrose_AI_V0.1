"""RLS tenant isolation on real Postgres via the non-privileged agent_runtime_app
role (MASTER-FR-001, AC-14). The app role only ever sees its tenant's rows."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.domain.entities import Proposal, Run, new_uuid, now
from app.store.sql import SqlStore
from tests.conftest import TENANT_A, TENANT_B

pytestmark = pytest.mark.integration


def _run(tenant):
    return Run(run_id=new_uuid(), tenant_id=tenant, session_id=new_uuid(),
               agent_key="case-triage", agent_version=1, temporal_workflow_id=None,
               status="running", principal_type="user_obo", obo_sub="u-1")


def _proposal(tenant, run_id):
    from datetime import timedelta
    return Proposal(
        proposal_id=new_uuid(), tenant_id=tenant, session_id=None, run_id=run_id,
        agent_key="case-triage", agent_version=1, obo_user="u-1",
        tool_id="case.apply_disposition", tool_version="1.0.0", tier="write-proposal",
        side_effects="reversible", args={"case_id": "c-1"}, rationale="r",
        affected_urns=[f"wr:{tenant}:case:case/c-1"], predicted_effect={},
        expires_at=now() + timedelta(days=7), status="pending")


async def test_rls_blocks_cross_tenant_reads(app_session_factory):
    store = SqlStore(app_session_factory)
    ra = _run(TENANT_A)
    await store.create_run(ra)
    pa = _proposal(TENANT_A, ra.run_id)
    await store.create_proposal(pa)

    # same tenant sees it
    assert await store.get_run(TENANT_A, ra.run_id) is not None
    assert await store.get_proposal(TENANT_A, pa.proposal_id) is not None
    # cross tenant: RLS hides the row -> None (404 shape upstream)
    assert await store.get_run(TENANT_B, ra.run_id) is None
    assert await store.get_proposal(TENANT_B, pa.proposal_id) is None


async def test_rls_enforced_at_role_level(app_session_factory):
    """Even a raw query under the non-privileged role, scoped to tenant B, cannot
    see tenant A's rows — RLS is enforced by the policy, not just the WHERE."""
    store = SqlStore(app_session_factory)
    ra = _run(TENANT_A)
    await store.create_run(ra)

    async with app_session_factory() as s:
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_B})
        rows = (await s.execute(text("SELECT count(*) c FROM runs"))).mappings().first()
        # RLS restricts the whole table to tenant B; tenant A's run is invisible.
        assert rows["c"] == 0
