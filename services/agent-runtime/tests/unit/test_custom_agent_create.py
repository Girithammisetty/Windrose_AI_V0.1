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


def _op_auth(sub="u-op", tenant=TENANT_A):
    return {"Authorization": f"Bearer {make_token(sub=sub, tenant_id=tenant, scopes=['operator'])}"}


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


async def test_guardrail_policy_persisted_and_clamped(client_and_container):
    """BRD 53 inc2: the security envelope (data_scope/budget/pii) is validated,
    the budget is clamped to the platform ceiling, and it is stored on the tenant
    config for the graph to enforce."""
    client, c = client_and_container
    body = {**_VALID,
            "data_scope": {"workspaces": ["019f62c1-0f5e-7af0-b9be-cbe343ea0ad4"]},
            "budget": {"max_tokens_per_session": 10_000_000},  # over the ceiling
            "pii": {"block_pii_egress": True}}
    r = await client.post("/api/v1/registry/tenants/self/agents", json=body, headers=_auth())
    assert r.status_code == 200, r.text
    gp = r.json()["data"]["guardrail_policy"]
    assert gp["data_scope"]["workspaces"] == ["019f62c1-0f5e-7af0-b9be-cbe343ea0ad4"]
    assert gp["budget"]["max_tokens_per_session"] == 200_000  # clamped DOWN (BR-8)
    assert gp["pii"]["block_pii_egress"] is True

    key = r.json()["data"]["agent_key"]
    cfg = await c.store.get_tenant_config(TENANT_A, key)
    assert cfg.guardrail_policy["budget"]["max_tokens_per_session"] == 200_000


async def test_rejects_non_uuid_workspace_scope(client_and_container):
    client, _ = client_and_container
    bad = {**_VALID, "data_scope": {"workspaces": ["not-a-uuid"]}}
    r = await client.post("/api/v1/registry/tenants/self/agents", json=bad, headers=_auth())
    assert r.status_code >= 400
    assert "workspaces" in r.text


async def test_rejects_budget_below_floor(client_and_container):
    client, _ = client_and_container
    bad = {**_VALID, "budget": {"max_tokens_per_session": 10}}
    r = await client.post("/api/v1/registry/tenants/self/agents", json=bad, headers=_auth())
    assert r.status_code >= 400
    assert "max_tokens_per_session" in r.text


async def test_autobind_persona_copilots_is_idempotent(client_and_container):
    """PA-FR-010: binding a set of roles provisions one persona copilot each;
    re-running only fills gaps (idempotent by deterministic key)."""
    client, c = client_and_container
    r = await client.post("/api/v1/registry/tenants/self/personas/autobind",
                          json={"roles": ["Claims Analyst", "SIU Investigator"]}, headers=_auth())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert {x["role"] for x in d["created"]} == {"Claims Analyst", "SIU Investigator"}
    assert d["skipped"] == []

    # Each bound copilot exists, is persona-grounded, advisory (no propose tool).
    for x in d["created"]:
        cfg = await c.store.get_tenant_config(TENANT_A, x["agent_key"])
        assert cfg.enabled
        assert cfg.prompt_params["propose_tool"] is None       # advisory by default
    defn = await c.store.get_agent_definition(d["created"][0]["agent_key"])
    assert defn.owner_tenant == TENANT_A                        # tenant-scoped

    # Re-run with an overlapping set: existing ones are skipped, new one created.
    r2 = await client.post("/api/v1/registry/tenants/self/personas/autobind",
                           json={"roles": ["Claims Analyst", "Auditor"]}, headers=_auth())
    d2 = r2.json()["data"]
    assert [x["role"] for x in d2["created"]] == ["Auditor"]
    assert [x["role"] for x in d2["skipped"]] == ["Claims Analyst"]


async def test_autobind_requires_agent_admin_and_roles(client_and_container):
    client, _ = client_and_container
    # non-admin
    r = await client.post("/api/v1/registry/tenants/self/personas/autobind",
                          json={"roles": ["X"]}, headers=_auth(sub="u-user"))
    assert r.status_code == 403
    # empty roles
    r2 = await client.post("/api/v1/registry/tenants/self/personas/autobind",
                           json={"roles": []}, headers=_auth())
    assert r2.status_code >= 400 and "roles" in r2.text


async def test_operator_ceiling_clamps_custom_agent_budget(client_and_container):
    """BRD 53 inc3 / BR-8: an operator lowers the platform budget ceiling; a
    subsequent tenant custom agent is clamped DOWN to it (not the 200k default)."""
    client, _ = client_and_container
    # Operator tightens the ceiling to 5,000 tokens.
    rc = await client.put("/api/v1/registry/platform/agent-ceilings",
                          json={"max_budget_tokens": 5000, "max_tier": "write-proposal"},
                          headers=_op_auth())
    assert rc.status_code == 200, rc.text

    # A tenant asks for 50,000 — it is clamped to the operator ceiling.
    body = {**_VALID, "budget": {"max_tokens_per_session": 50000}}
    r = await client.post("/api/v1/registry/tenants/self/agents", json=body, headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["data"]["guardrail_policy"]["budget"]["max_tokens_per_session"] == 5000


async def test_agent_ceilings_are_operator_only(client_and_container):
    client, _ = client_and_container
    # tenant admin (not operator) cannot read or set ceilings
    r = await client.get("/api/v1/registry/platform/agent-ceilings", headers=_auth())
    assert r.status_code == 403
    r2 = await client.put("/api/v1/registry/platform/agent-ceilings",
                          json={"max_budget_tokens": 5000}, headers=_auth())
    assert r2.status_code == 403
    # operator can read defaults
    r3 = await client.get("/api/v1/registry/platform/agent-ceilings", headers=_op_auth())
    assert r3.status_code == 200
    assert r3.json()["data"]["max_budget_tokens"] == 200000


async def test_retrain_watch_crud(client_and_container):
    """BRD 52 inc3: register a scheduled drift watch, list it, delete it."""
    client, c = client_and_container
    body = {"model_urn": "wr:t:experiment:model/m-scorer", "watched_agent_key": "case-triage",
            "drift_threshold": 0.25, "min_corrections": 15, "cadence_seconds": 3600}
    r = await client.post("/api/v1/registry/retrain-watches", json=body, headers=_auth())
    assert r.status_code == 200, r.text
    w = r.json()["data"]
    assert w["model_urn"] == "wr:t:experiment:model/m-scorer"
    assert w["drift_threshold"] == 0.25 and w["min_corrections"] == 15

    lr = await client.get("/api/v1/registry/retrain-watches", headers=_auth())
    assert any(x["id"] == w["id"] for x in lr.json()["data"])

    dr = await client.delete(f"/api/v1/registry/retrain-watches/{w['id']}", headers=_auth())
    assert dr.status_code == 200 and dr.json()["data"]["deleted"] is True
    lr2 = await client.get("/api/v1/registry/retrain-watches", headers=_auth())
    assert not any(x["id"] == w["id"] for x in lr2.json()["data"])


async def test_retrain_watch_requires_fields_and_cap(client_and_container):
    client, _ = client_and_container
    # non-admin
    r = await client.post("/api/v1/registry/retrain-watches",
                          json={"model_urn": "x", "watched_agent_key": "y"}, headers=_auth(sub="u-user"))  # noqa: E501
    assert r.status_code == 403
    # missing watched_agent_key
    r2 = await client.post("/api/v1/registry/retrain-watches",
                           json={"model_urn": "x"}, headers=_auth())
    assert r2.status_code >= 400 and "watched_agent_key" in r2.text


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
