"""Tier 2b registry/read surfaces: agent catalog browse (GET /registry/agents,
GET /registry/agents/{key}/versions), per-tenant config read
(GET /tenants/self/agents/{key}) and the tenant run-history list (GET /runs).
Exercises the route-level authz (tenant admin for control-plane reads; any
tenant principal for the run list) over the in-memory store double."""

from __future__ import annotations

import httpx
import pytest

from app.agents.catalog import seed_catalog
from app.container import build_container
from app.domain.entities import Run, new_uuid
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, make_settings, make_token


class _CapAuthz:
    """Capability-based authz double (P4): the agent-admin routes now authorize on
    rbac capabilities via OPA, not a JWT scope. Models "u-admin holds the
    ai.agent.* capabilities (e.g. via an Admin/custom role), u-user does not"."""

    async def allow(self, *, subject, action, tenant, resource_urn=None, workspace_id=None):
        return subject.get("id") == "u-admin" and action.startswith("ai.agent.")


@pytest.fixture
async def client_and_container():
    c = build_container(make_settings(), mode="memory", authz=_CapAuthz())
    await seed_catalog(c.store, c.signing_key)
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin() -> str:
    # No JWT scope — authorization now comes from the rbac capability (the
    # _CapAuthz double grants u-admin the ai.agent.* caps).
    return make_token(sub="u-admin", tenant_id=TENANT_A, scopes=[])


def _user() -> str:
    return make_token(sub="u-user", tenant_id=TENANT_A, scopes=[])


async def test_list_agents_returns_seeded_catalog(client_and_container):
    client, _ = client_and_container
    r = await client.get("/api/v1/registry/agents", headers=_auth(_admin()))
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    keys = {row["agent_key"] for row in rows}
    assert "case-triage" in keys
    triage = next(row for row in rows if row["agent_key"] == "case-triage")
    assert triage["display_name"]
    assert triage["latest_published_version"] == 1
    # Sorted by agent_key for a stable browse order.
    assert [row["agent_key"] for row in rows] == sorted(keys)


async def test_list_agents_requires_tenant_admin(client_and_container):
    client, _ = client_and_container
    r = await client.get("/api/v1/registry/agents", headers=_auth(_user()))
    assert r.status_code == 403


async def test_list_versions_for_agent(client_and_container):
    client, _ = client_and_container
    r = await client.get("/api/v1/registry/agents/case-triage/versions",
                         headers=_auth(_admin()))
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert rows and rows[0]["version"] == 1
    assert rows[0]["status"] == "published"
    assert rows[0]["graph_ref"]


async def test_list_versions_unknown_agent_404(client_and_container):
    client, _ = client_and_container
    r = await client.get("/api/v1/registry/agents/nope/versions", headers=_auth(_admin()))
    assert r.status_code == 404


async def test_tenant_config_roundtrip(client_and_container):
    client, _ = client_and_container
    admin = _admin()

    # Unconfigured agent reports the runtime defaults, flagged configured=false.
    r = await client.get("/api/v1/registry/tenants/self/agents/case-triage", headers=_auth(admin))
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["configured"] is False
    assert body["enabled"] is True

    # PUT then read back the persisted row.
    r = await client.put("/api/v1/registry/tenants/self/agents/case-triage", headers=_auth(admin),
                         json={"enabled": False, "self_approval": False})
    assert r.status_code == 200, r.text

    r = await client.get("/api/v1/registry/tenants/self/agents/case-triage", headers=_auth(admin))
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["configured"] is True
    assert body["enabled"] is False


async def test_tenant_config_read_requires_tenant_admin(client_and_container):
    client, _ = client_and_container
    r = await client.get("/api/v1/registry/tenants/self/agents/case-triage", headers=_auth(_user()))
    assert r.status_code == 403


async def test_list_runs_is_tenant_scoped_and_newest_first(client_and_container):
    client, c = client_and_container
    # Two runs in tenant A, one in tenant B — B's must never appear for A.
    a1 = Run(run_id=new_uuid(), tenant_id=TENANT_A, session_id=new_uuid(),
             agent_key="case-triage", agent_version=1, temporal_workflow_id=None,
             status="succeeded", principal_type="user_obo")
    a2 = Run(run_id=new_uuid(), tenant_id=TENANT_A, session_id=new_uuid(),
             agent_key="analytics", agent_version=1, temporal_workflow_id=None,
             status="running", principal_type="user_obo")
    b1 = Run(run_id=new_uuid(), tenant_id=TENANT_B, session_id=new_uuid(),
             agent_key="case-triage", agent_version=1, temporal_workflow_id=None,
             status="succeeded", principal_type="user_obo")
    await c.store.create_run(a1)
    await c.store.create_run(a2)
    await c.store.create_run(b1)

    r = await client.get("/api/v1/runs", headers=_auth(_user()))
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [row["id"] for row in body["data"]]
    assert a1.run_id in ids and a2.run_id in ids
    assert b1.run_id not in ids
    assert body["page"] == {"next_cursor": None, "has_more": False}
    assert body["data"][0]["created_at"]  # run_view now carries created_at

    # filter[agent_key] narrows the list.
    r = await client.get("/api/v1/runs", params={"filter[agent_key]": "analytics"},
                         headers=_auth(_user()))
    assert [row["id"] for row in r.json()["data"]] == [a2.run_id]
