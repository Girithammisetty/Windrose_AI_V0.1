"""Verified queries: lifecycle, read-only gate, semantic search
(SEM-FR-040..043, BR-11/BR-14, AC-11/AC-12)."""

from __future__ import annotations

from tests.conftest import TENANT_B, WORKSPACE, auth

VQ = {
    "workspace_id": WORKSPACE,
    "nl_text": "monthly revenue by region for the last year",
    "sql_text": "SELECT date_trunc('month', order_date) m, region, sum(order_total) "
                "FROM {{dataset('Orders')}} WHERE order_date >= :start GROUP BY 1, 2",
    "variables": [{"name": "start", "type": "date", "required": True}],
    "tags": ["revenue"],
}


async def create_vq(client, tenant=None, **overrides) -> dict:
    resp = await client.post("/api/v1/verified-queries", json={**VQ, **overrides},
                             headers=auth(tenant) if tenant else auth())
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def approve_vq(client, vq_id: str) -> None:
    resp = await client.post(f"/api/v1/verified-queries/{vq_id}/submit",
                             headers=auth(sub="author-1"))
    assert resp.status_code == 200
    resp = await client.post(f"/api/v1/verified-queries/{vq_id}/approve",
                             headers=auth(sub="steward-1"))
    assert resp.status_code == 200, resp.text


async def test_lifecycle_draft_to_approved(client, container):
    vq = await create_vq(client)
    assert vq["status"] == "draft"
    await approve_vq(client, vq["id"])
    resp = await client.get(f"/api/v1/verified-queries/{vq['id']}", headers=auth())
    data = resp.json()["data"]
    assert data["status"] == "approved"
    assert data["approved_by"] == "steward-1"
    assert data["decided_at"] is not None
    assert container.memory_state.events_of_type("verified_query.approved")


async def test_ac11_read_only_violation_rejected():
    import pytest

    from app.domain.errors import ValidationFailed
    from app.domain.sqlguard import validate_read_only_sql
    for sql in (
        "UPDATE orders SET x = 1",
        "SELECT 1; DELETE FROM orders",
        "WITH x AS (SELECT 1) INSERT INTO y SELECT * FROM x",
        "DROP TABLE orders",
    ):
        with pytest.raises(ValidationFailed):
            validate_read_only_sql(sql)


async def test_update_sql_rejected_over_http(client):
    resp = await client.post(
        "/api/v1/verified-queries",
        json={**VQ, "sql_text": "UPDATE orders SET order_total = 0"},
        headers=auth())
    assert resp.status_code == 422


async def test_author_cannot_approve_own(client):
    vq = await create_vq(client)
    await client.post(f"/api/v1/verified-queries/{vq['id']}/submit",
                      headers=auth(sub="author-1"))
    # submitted_by is the creator (user-1 by default): use same sub
    resp = await client.post(f"/api/v1/verified-queries/{vq['id']}/approve",
                             headers=auth(sub="user-1"))
    assert resp.status_code == 403


async def test_reject_then_revise(client):
    vq = await create_vq(client)
    await client.post(f"/api/v1/verified-queries/{vq['id']}/submit", headers=auth())
    resp = await client.post(f"/api/v1/verified-queries/{vq['id']}/reject",
                             json={"note": "too broad"}, headers=auth(sub="steward-1"))
    assert resp.status_code == 200
    resp = await client.patch(f"/api/v1/verified-queries/{vq['id']}",
                              json={"nl_text": "narrower question"}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "draft"


async def test_search_returns_only_approved_ranked(client):
    vq1 = await create_vq(client)
    await approve_vq(client, vq1["id"])
    await create_vq(client, nl_text="customer churn by tier",
                    sql_text="SELECT tier, count(*) FROM {{dataset('Customers')}} "
                             "GROUP BY 1")  # stays draft
    resp = await client.get(
        "/api/v1/verified-queries/search",
        params={"q": "revenue by region monthly", "workspace_id": WORKSPACE},
        headers=auth())
    assert resp.status_code == 200
    results = resp.json()["data"]
    assert [r["id"] for r in results] == [vq1["id"]]
    assert results[0]["score"] > 0.3


async def test_ac12_search_is_tenant_scoped_and_audited(client, container):
    vq = await create_vq(client)
    await approve_vq(client, vq["id"])
    # agent OBO token of tenant B sees nothing (BR-14: hard filter, empty result)
    resp = await client.post(
        "/api/v1/tools/search_verified_queries",
        json={"q": "revenue by region monthly", "workspace_id": WORKSPACE},
        headers=auth(TENANT_B, typ="agent_obo", agent_id="analytics-agent",
                     agent_version="1", obo_sub="user-9"))
    assert resp.status_code == 200
    assert resp.json()["data"]["results"] == []
    audits = container.memory_state.events_of_type("ai.tool_invoked.v1")
    assert audits and audits[-1]["payload"]["tool"] == "search_verified_queries"
    assert audits[-1]["via_agent"] == {"agent_id": "analytics-agent", "version": "1"}


async def test_archived_excluded_from_search(client):
    vq = await create_vq(client)
    await approve_vq(client, vq["id"])
    resp = await client.post(f"/api/v1/verified-queries/{vq['id']}/archive",
                             headers=auth())
    assert resp.status_code == 200
    resp = await client.get(
        "/api/v1/verified-queries/search",
        params={"q": "revenue", "workspace_id": WORKSPACE}, headers=auth())
    assert resp.json()["data"] == []


async def test_candidates_endpoint_creates_draft_with_provenance(client):
    resp = await client.post(
        "/api/v1/verified-queries/candidates",
        json={**VQ, "agent_run_urn": "wr:t:agent:run/018f-run"},
        headers=auth())
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["status"] == "draft"
    assert data["provenance"] == {"agent_run_urn": "wr:t:agent:run/018f-run",
                                  "origin": "harvested"}


async def test_list_filters(client):
    vq = await create_vq(client)
    await approve_vq(client, vq["id"])
    await create_vq(client, nl_text="another draft one")
    resp = await client.get(
        "/api/v1/verified-queries?filter[status]=approved", headers=auth())
    assert [v["status"] for v in resp.json()["data"]] == ["approved"]
