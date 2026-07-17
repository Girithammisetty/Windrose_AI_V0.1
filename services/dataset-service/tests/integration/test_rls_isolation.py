"""Integration: Postgres RLS tenant-isolation suite (MASTER-FR-001/004, AC-13)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import TENANT_A, TENANT_B, auth, create_dataset

pytestmark = pytest.mark.integration


class TestRlsIsolation:
    async def test_ac13_list_never_shows_foreign_rows(self, client):
        """AC-13: tenant A's token never sees tenant B rows, crafted filters or not."""
        await create_dataset(client, tenant=TENANT_A, name="A-Data", tags=["sales"])
        await create_dataset(client, tenant=TENANT_B, name="B-Data", tags=["sales"])
        for params in ("", "?filter[tags]=sales", "?q=B-Data", "?filter[status]=draft"):
            resp = await client.get(f"/api/v1/datasets{params}", headers=auth(TENANT_A))
            names = [d["name"] for d in resp.json()["data"]]
            assert "B-Data" not in names, f"leak via {params!r}"

    async def test_cross_tenant_reads_and_writes_404(self, client):
        ds = await create_dataset(client, tenant=TENANT_A, name="A-Only")
        for method, url, body in [
            ("GET", f"/api/v1/datasets/{ds['id']}", None),
            ("PATCH", f"/api/v1/datasets/{ds['id']}", {"description": "x"}),
            ("DELETE", f"/api/v1/datasets/{ds['id']}", None),
            ("POST", f"/api/v1/datasets/{ds['id']}/restore", None),
            ("GET", f"/api/v1/datasets/{ds['id']}/versions", None),
            ("GET", f"/api/v1/datasets/{ds['id']}/profile", None),
            ("GET", f"/api/v1/datasets/{ds['id']}/consumers", None),
        ]:
            resp = await client.request(method, url, json=body, headers=auth(TENANT_B))
            assert resp.status_code == 404, f"{method} {url} -> {resp.status_code}"

    async def test_rls_enforced_at_sql_level(self, client, engine):
        """Belt-and-braces: the app role cannot see foreign rows even with raw SQL."""
        await create_dataset(client, tenant=TENANT_A, name="RawSql")
        async with engine.connect() as conn:
            # no tenant context -> zero rows visible
            count = (await conn.execute(text("SELECT count(*) FROM datasets"))).scalar()
            assert count == 0
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_B}
            )
            count = (await conn.execute(text("SELECT count(*) FROM datasets"))).scalar()
            assert count == 0
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A}
            )
            count = (await conn.execute(text("SELECT count(*) FROM datasets"))).scalar()
            assert count == 1

    async def test_rls_insert_check_blocks_wrong_tenant(self, engine):
        """WITH CHECK side of the policy: cannot insert a row for another tenant."""
        from sqlalchemy.exc import DBAPIError

        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A}
            )
            with pytest.raises(DBAPIError):
                await conn.execute(
                    text(
                        "INSERT INTO datasets (id, tenant_id, workspace_id, name, "
                        "iceberg_table, created_by, created_at, updated_at) VALUES "
                        "(gen_random_uuid(), :tb, gen_random_uuid(), 'evil', 't', 'x', "
                        "now(), now())"
                    ),
                    {"tb": TENANT_B},
                )

    async def test_lineage_edges_isolated(self, client):
        t = TENANT_A
        resp = await client.post(
            "/api/v1/lineage/edges",
            json={"from_urn": f"wr:{t}:ingestion:ingestion/i1",
                  "to_urn": f"wr:{t}:dataset:dataset/d1", "activity": "ingested"},
            headers=auth(t),
        )
        assert resp.status_code == 201
        resp = await client.get(
            "/api/v1/lineage",
            params={"urn": f"wr:{t}:dataset:dataset/d1", "direction": "upstream"},
            headers=auth(TENANT_B),
        )
        assert resp.status_code == 404
