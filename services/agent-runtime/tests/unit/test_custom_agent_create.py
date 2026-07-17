"""BRD 53 PA-FR-050/060: tenant-authored custom agents via
POST /registry/tenants/self/agents. Verifies envelope validation, tenant
isolation (owner_tenant), the forced shared graph_ref, and that the created
agent is published + tenant-enabled with its allow-list as the enforced
toolset."""

from __future__ import annotations

import httpx
import pytest

from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, make_settings, make_token


class _CapAuthz:
    async def allow(self, *, subject, action, tenant, resource_urn=None, workspace_id=None):
        return subject.get("id") == "u-admin" and action.startswith("ai.agent.")


@pytest.fixture
async def client_and_container():
    c = build_container(make_settings(), mode="memory", authz=_CapAuthz())
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c


def _auth(sub="u-admin", tenant=TENANT_A):
    return {"Authorization": f"Bearer {make_token(sub=sub, tenant_id=tenant, scopes=[])}"}


_VALID = {"display_name": "Reg E Disposition Copilot",
          "persona": "Dispute Intake Analyst",
          "system_prompt": "Prioritise Reg E deadlines; be conservative.",
          "allowed_tools": ["case.apply_disposition"],
          "propose_tool": "case.apply_disposition"}


async def test_create_custom_agent_publishes_tenant_scoped(client_and_container):
    client, c = client_and_container
    r = await client.post("/api/v1/registry/tenants/self/agents",
                          json=_VALID, headers=_auth())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "published"
    assert d["graph_ref"] == "persona_copilot.v1"       # forced shared graph
    assert d["owner_tenant"] == TENANT_A
    key = d["agent_key"]

    # The version's toolset == the allow-list (what ProposalService enforces).
    v = await c.store.get_agent_version(key, 1)
    assert [t["tool_id"] for t in v.toolset] == ["case.apply_disposition"]
    assert v.graph_ref == "persona_copilot.v1"
    # Enabled + persona/prompt/propose_tool carried in the tenant config.
    cfg = await c.store.get_tenant_config(TENANT_A, key)
    assert cfg.enabled and cfg.prompt_params["persona"] == "Dispute Intake Analyst"
    assert cfg.prompt_params["propose_tool"] == "case.apply_disposition"


async def test_create_requires_agent_admin(client_and_container):
    client, _ = client_and_container
    r = await client.post("/api/v1/registry/tenants/self/agents",
                          json=_VALID, headers=_auth(sub="u-user"))
    assert r.status_code == 403


async def test_rejects_empty_allowlist(client_and_container):
    client, _ = client_and_container
    bad = {**_VALID, "allowed_tools": []}
    r = await client.post("/api/v1/registry/tenants/self/agents", json=bad, headers=_auth())
    assert r.status_code >= 400
    assert "allowed_tools" in r.text


async def test_rejects_propose_tool_outside_allowlist(client_and_container):
    client, _ = client_and_container
    bad = {**_VALID, "propose_tool": "experiment.model.promote"}
    r = await client.post("/api/v1/registry/tenants/self/agents", json=bad, headers=_auth())
    assert r.status_code >= 400
    assert "allowed_tools" in r.text


async def test_rejects_tier_above_ceiling(client_and_container):
    client, _ = client_and_container
    bad = {**_VALID, "max_tier": "write-direct"}
    r = await client.post("/api/v1/registry/tenants/self/agents", json=bad, headers=_auth())
    assert r.status_code >= 400
    assert "write-proposal" in r.text


async def test_rejects_foreign_graph_ref(client_and_container):
    client, _ = client_and_container
    bad = {**_VALID, "graph_ref": "triage.v1"}
    r = await client.post("/api/v1/registry/tenants/self/agents", json=bad, headers=_auth())
    assert r.status_code >= 400


async def test_isolation_other_tenant_cannot_see_it(client_and_container):
    client, _ = client_and_container
    r = await client.post("/api/v1/registry/tenants/self/agents", json=_VALID, headers=_auth())
    key = r.json()["data"]["agent_key"]
    # TENANT_A admin lists it...
    la = await client.get("/api/v1/registry/agents", headers=_auth())
    assert any(a["agent_key"] == key for a in la.json()["data"])
    # ...TENANT_B admin (grant them the cap in the double via sub=u-admin) does NOT.
    lb = await client.get("/api/v1/registry/agents", headers=_auth(tenant=TENANT_B))
    assert not any(a["agent_key"] == key for a in lb.json()["data"])
