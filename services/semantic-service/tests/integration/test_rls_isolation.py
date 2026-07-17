"""Postgres RLS isolation suite (MASTER-FR-001/003/004, AC-14) against the
non-privileged application role."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import TENANT_A, TENANT_B, WORKSPACE, auth, create_published_model

pytestmark = pytest.mark.integration


async def test_rls_hides_foreign_tenant_rows_at_sql_level(client, container):
    model = await create_published_model(client)  # tenant A
    session_factory = container.extras["session_factory"]

    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_B})
        rows = (await session.execute(text("SELECT id FROM semantic_models"))).all()
        assert rows == []  # tenant B session sees nothing

    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
        rows = (await session.execute(text("SELECT id FROM semantic_models"))).all()
        assert [str(r[0]) for r in rows] == [model["id"]]


async def test_rls_blocks_unbound_sessions(container):
    session_factory = container.extras["session_factory"]
    async with session_factory() as session:  # no app.tenant_id GUC set
        rows = (await session.execute(text("SELECT id FROM semantic_models"))).all()
        assert rows == []


async def test_ac14_api_cross_tenant_404_with_audit_row(client, container):
    model = await create_published_model(client)
    resp = await client.post(
        "/api/v1/compile",
        json={"model": model["id"], "metrics": ["revenue"], "dialect": "trino"},
        headers=auth(TENANT_B))
    assert resp.status_code == 404
    session_factory = container.extras["session_factory"]
    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_B})
        rows = (await session.execute(text(
            "SELECT event_type FROM outbox "
            "WHERE event_type = 'security.cross_tenant_denied'"))).all()
        assert rows  # audit event landed in tenant B's outbox partition


async def test_isolation_every_write_path(client):
    model = await create_published_model(client)
    b = auth(TENANT_B)
    for method, path, body in [
        ("PATCH", f"/api/v1/models/{model['id']}", {"description": "hijack"}),
        ("DELETE", f"/api/v1/models/{model['id']}", None),
        ("POST", f"/api/v1/models/{model['id']}/versions", {}),
        ("POST", f"/api/v1/models/{model['id']}/bootstrap", {"sources": {}}),
    ]:
        resp = await client.request(method, path, json=body, headers=b)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"
    # tenant A unaffected
    resp = await client.get(f"/api/v1/models/{model['id']}", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["data"]["description"] != "hijack"


async def test_verified_query_isolation(client):
    from tests.unit.test_verified_queries import create_vq

    vq = await create_vq(client)  # tenant A
    resp = await client.get(f"/api/v1/verified-queries/{vq['id']}",
                            headers=auth(TENANT_B))
    assert resp.status_code == 404
    resp = await client.get(
        "/api/v1/verified-queries", params={"filter[workspace_id]": WORKSPACE},
        headers=auth(TENANT_B))
    assert resp.json()["data"] == []
