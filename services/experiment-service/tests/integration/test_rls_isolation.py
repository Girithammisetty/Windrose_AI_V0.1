"""Integration (real Postgres RLS via a non-privileged role): tenant rows are
invisible outside their tenant context (MASTER-FR-001/003, AC-12)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import TENANT_A, TENANT_B, ctx_for, make_experiment

pytestmark = pytest.mark.integration


async def test_rls_hides_other_tenant_rows(container, engine):
    ctx = ctx_for(TENANT_A)
    exp = await make_experiment(container, ctx, name=f"rls-{uuid.uuid4().hex[:8]}")

    async with engine.connect() as conn:
        # tenant B context: the row is invisible (RLS on the non-priv role)
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                           {"t": TENANT_B})
        b_count = (await conn.execute(
            text("SELECT count(*) FROM experiments WHERE id = :i"), {"i": exp.id})).scalar()
        assert b_count == 0
        # tenant A context: visible
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                           {"t": TENANT_A})
        a_count = (await conn.execute(
            text("SELECT count(*) FROM experiments WHERE id = :i"), {"i": exp.id})).scalar()
        assert a_count == 1


async def test_cross_tenant_api_returns_404(client, container):
    from tests.conftest import auth

    ctx = ctx_for(TENANT_A)
    exp = await make_experiment(container, ctx, name=f"iso-{uuid.uuid4().hex[:8]}")
    resp = await client.get(f"/api/v1/experiments/{exp.id}", headers=auth(TENANT_B))
    assert resp.status_code == 404
