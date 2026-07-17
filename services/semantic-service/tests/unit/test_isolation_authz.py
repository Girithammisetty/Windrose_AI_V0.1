"""Tenant isolation (MASTER-FR-001..004, AC-14) + authz matrix (MASTER-FR-071).

The memory store is the in-memory tenant-policy fake required by
CONVENTIONS.md; the Postgres-RLS variant lives in tests/integration.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    TENANT_B,
    WORKSPACE,
    auth,
    create_published_model,
)


async def test_ac14_cross_tenant_compile_404_and_audited(client, container):
    model = await create_published_model(client)  # tenant A
    resp = await client.post(
        "/api/v1/compile",
        json={"model": model["id"], "metrics": ["revenue"], "dialect": "trino"},
        headers=auth(TENANT_B))
    assert resp.status_code == 404  # not 403 — no existence leak
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    audits = container.memory_state.events_of_type("security.cross_tenant_denied")
    assert audits and audits[-1]["tenant_id"] == TENANT_B
    assert model["id"] in audits[-1]["resource_urn"]


async def test_cross_tenant_reads_404_for_every_resource(client):
    model = await create_published_model(client)
    b = auth(TENANT_B)
    for method, path, body in [
        ("GET", f"/api/v1/models/{model['id']}", None),
        ("PATCH", f"/api/v1/models/{model['id']}", {"description": "x"}),
        ("DELETE", f"/api/v1/models/{model['id']}", None),
        ("GET", f"/api/v1/models/{model['id']}/versions", None),
        ("GET", f"/api/v1/models/{model['id']}/versions/1", None),
        ("POST", f"/api/v1/models/{model['id']}/versions", {}),
        ("POST", f"/api/v1/models/{model['id']}/versions/1/submit", {}),
        ("GET", f"/api/v1/models/{model['id']}/definition", None),
        ("POST", f"/api/v1/models/{model['id']}/bootstrap", {"sources": {}}),
    ]:
        resp = await client.request(method, path, json=body, headers=b)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"


async def test_cross_tenant_list_is_empty(client):
    await create_published_model(client)
    resp = await client.get(f"/api/v1/models?filter[workspace_id]={WORKSPACE}",
                            headers=auth(TENANT_B))
    assert resp.json()["data"] == []


async def test_unauthenticated_401(client):
    resp = await client.get("/api/v1/models")
    assert resp.status_code == 401
    resp = await client.get("/api/v1/models",
                            headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Authz matrix: every endpoint requires its action scope (403 without it,
# never-403 with it). Cases: (method, path, body, required_scope).

MATRIX = [
    ("POST", "/api/v1/models", {"workspace_id": WORKSPACE, "name": "m1"},
     "semantic.model.create"),
    ("GET", "/api/v1/models", None, "semantic.model.read"),
    ("GET", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff", None,
     "semantic.model.read"),
    ("PATCH", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff",
     {"description": "x"}, "semantic.model.update"),
    ("DELETE", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff", None,
     "semantic.model.delete"),
    ("GET", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff/versions", None,
     "semantic.model.read"),
    ("POST", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff/versions", {},
     "semantic.model.update"),
    ("PATCH", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff/versions/1",
     {"definition": {}}, "semantic.model.update"),
    ("POST", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff/versions/1/submit",
     {}, "semantic.model.update"),
    ("POST", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff/versions/1/approve",
     {}, "semantic.model.approve"),
    ("POST", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff/versions/1/reject",
     {"note": "n"}, "semantic.model.approve"),
    ("GET", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff/definition", None,
     "semantic.model.read"),
    ("POST", "/api/v1/models/018f0000-0000-7000-8000-0000000000ff/bootstrap",
     {"sources": {}}, "semantic.model.update"),
    ("GET", "/api/v1/operations/018f0000-0000-7000-8000-0000000000ff", None,
     "semantic.model.read"),
    ("POST", "/api/v1/compile",
     {"model": "m", "workspace_id": WORKSPACE, "metrics": ["revenue"]},
     "semantic.compile.execute"),
    ("POST", "/api/v1/compile/chart",
     {"model": "m", "workspace_id": WORKSPACE, "chart_type": "sankey_chart"},
     "semantic.compile.execute"),
    ("POST", "/api/v1/verified-queries",
     {"workspace_id": WORKSPACE, "nl_text": "q", "sql_text": "SELECT 1"},
     "semantic.verified_query.create"),
    ("GET", "/api/v1/verified-queries", None, "semantic.verified_query.read"),
    ("GET", "/api/v1/verified-queries/search?q=x&workspace_id=" + WORKSPACE, None,
     "semantic.verified_query.read"),
    ("GET", "/api/v1/verified-queries/018f0000-0000-7000-8000-0000000000ff", None,
     "semantic.verified_query.read"),
    ("PATCH", "/api/v1/verified-queries/018f0000-0000-7000-8000-0000000000ff",
     {"tags": []}, "semantic.verified_query.update"),
    ("POST", "/api/v1/verified-queries/018f0000-0000-7000-8000-0000000000ff/submit",
     {}, "semantic.verified_query.update"),
    ("POST", "/api/v1/verified-queries/018f0000-0000-7000-8000-0000000000ff/approve",
     {}, "semantic.verified_query.approve"),
    ("POST", "/api/v1/verified-queries/018f0000-0000-7000-8000-0000000000ff/reject",
     {"note": "n"}, "semantic.verified_query.approve"),
    ("POST", "/api/v1/verified-queries/018f0000-0000-7000-8000-0000000000ff/archive",
     {}, "semantic.verified_query.update"),
    ("POST", "/api/v1/verified-queries/candidates",
     {"workspace_id": WORKSPACE, "nl_text": "q", "sql_text": "SELECT 1",
      "agent_run_urn": "wr:t:agent:run/1"}, "semantic.verified_query.create"),
    ("GET", "/api/v1/tools", None, "semantic.model.read"),
    ("POST", "/api/v1/tools/get_metrics", {}, "semantic.model.read"),
    ("POST", "/api/v1/tools/get_dimensions", {}, "semantic.model.read"),
    ("POST", "/api/v1/tools/compile_metric_sql",
     {"model": "m", "workspace_id": WORKSPACE, "metrics": ["revenue"]},
     "semantic.compile.execute"),
    ("POST", "/api/v1/tools/search_verified_queries",
     {"workspace_id": WORKSPACE, "q": "x"}, "semantic.verified_query.read"),
]


@pytest.mark.parametrize(("method", "path", "body", "scope"), MATRIX)
async def test_authz_matrix(client, method, path, body, scope):
    # without the scope -> 403
    resp = await client.request(method, path, json=body,
                                headers=auth(scopes=["unrelated.scope"]))
    assert resp.status_code == 403, f"{method} {path}: expected 403"
    assert resp.json()["error"]["code"] == "PERMISSION_DENIED"
    # with the scope -> anything but 401/403 (resource may 404/409/422)
    resp = await client.request(method, path, json=body,
                                headers=auth(scopes=[scope]))
    assert resp.status_code not in (401, 403), \
        f"{method} {path}: got {resp.status_code} with scope {scope}"
