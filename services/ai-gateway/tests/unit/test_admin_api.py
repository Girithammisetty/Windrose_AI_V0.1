"""Admin plane: providers CRUD + drain + last-deployment guard, ladders,
budgets CRUD (+ soft warning), keys, guardrails RBAC, spend, idempotency,
AC-12 cross-tenant behavior."""

from __future__ import annotations

from tests.conftest import (
    TENANT_A,
    TENANT_B,
    WORKSPACE,
    admin_auth,
    dp_headers,
    mint_key,
    seed_default_deployments,
    seed_deployment,
)

PROVIDER_BODY = {
    "provider": "bedrock", "model_family": "fast-small",
    "deployment_name": "claude-haiku-use1", "region": "us-east-1",
    "cloud": "aws", "endpoint_vault_ref": "secret/ai/bedrock/haiku",
    "priority": 10,
}


async def test_provider_crud_and_state_machine(client, container):
    r = await client.post("/api/v1/admin/providers", json=PROVIDER_BODY,
                          headers=admin_auth())
    assert r.status_code == 201
    dep = r.json()["data"]
    assert dep["status"] == "active"

    r = await client.get("/api/v1/admin/providers", headers=admin_auth())
    assert r.status_code == 200
    assert r.json()["data"][0]["circuit_state"] == "closed"
    assert "page" in r.json()

    # drain → disabled → active
    r = await client.post(f"/api/v1/admin/providers/{dep['id']}/drain?force=true",
                          headers=admin_auth())
    assert r.json()["data"]["status"] == "draining"
    r = await client.patch(f"/api/v1/admin/providers/{dep['id']}?force=true",
                           json={"status": "disabled"}, headers=admin_auth())
    assert r.json()["data"]["status"] == "disabled"
    r = await client.patch(f"/api/v1/admin/providers/{dep['id']}",
                           json={"status": "active"}, headers=admin_auth())
    assert r.json()["data"]["status"] == "active"
    # illegal transition
    r = await client.patch(f"/api/v1/admin/providers/{dep['id']}?force=true",
                           json={"status": "disabled"}, headers=admin_auth())
    assert r.json()["data"]["status"] == "disabled"
    r = await client.post(f"/api/v1/admin/providers/{dep['id']}/drain",
                          headers=admin_auth())
    assert r.status_code == 409

    events = container.bus.events_of_type("provider.state_changed")
    assert [ (e["payload"]["from"], e["payload"]["to"]) for e in events ][:2] == [
        ("active", "draining"), ("draining", "disabled"),
    ]


async def test_last_deployment_guard_409(client, container):
    d = await seed_deployment(container, alias="fast-small", name="only-one")
    r = await client.post(f"/api/v1/admin/providers/{d.id}/drain",
                          headers=admin_auth())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"
    r = await client.post(f"/api/v1/admin/providers/{d.id}/drain?force=true",
                          headers=admin_auth())
    assert r.status_code == 200


async def test_provider_requires_operator_scope(client, container):
    r = await client.post("/api/v1/admin/providers", json=PROVIDER_BODY,
                          headers=admin_auth(scopes=["ai.provider.write"]))
    assert r.status_code == 403  # ai.platform.admin missing


async def test_ladder_platform_put_and_tenant_override(client, container):
    rungs = [
        {"model_alias": "fast-small", "max_tokens": 4096,
         "temperature_default": 0.1, "cost_tier": 1},
        {"model_alias": "balanced", "max_tokens": 8192,
         "temperature_default": 0.1, "cost_tier": 2},
    ]
    r = await client.put("/api/v1/admin/ladders/sql-gen",
                         json={"rungs": rungs, "scope": "platform"},
                         headers=admin_auth())
    assert r.status_code == 200
    assert r.json()["data"]["version"] == 1

    # tenant override caps at rung 0
    r = await client.put("/api/v1/admin/ladders/sql-gen",
                         json={"rungs": rungs, "scope": "tenant", "max_rung": 0},
                         headers=admin_auth())
    assert r.status_code == 200
    r = await client.get("/api/v1/admin/ladders/sql-gen", headers=admin_auth())
    assert r.json()["data"]["scope"] == "tenant"
    assert r.json()["data"]["max_rung"] == 0
    assert container.bus.events_of_type("ladder.updated")

    # ladder cap now enforced on the data plane
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "balanced",
              "messages": [{"role": "user", "content": "q"}]},
        headers=dp_headers(secret, request_class="sql-gen"),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "LADDER_CAP"


async def test_ladder_validation(client, container):
    r = await client.put("/api/v1/admin/ladders/chat",
                         json={"rungs": [{"model_alias": "x"}],
                               "scope": "platform"},
                         headers=admin_auth())
    assert r.status_code == 422
    r = await client.put("/api/v1/admin/ladders/nope",
                         json={"rungs": [], "scope": "platform"},
                         headers=admin_auth())
    assert r.status_code == 422


async def test_budget_crud_with_soft_warning(client, container):
    r = await client.post("/api/v1/admin/budgets", json={
        "scope_type": "tenant", "scope_ref": TENANT_A, "window": "monthly",
        "limit_usd": 100.0,
    }, headers=admin_auth())
    assert r.status_code == 201

    # child exceeding parent → created with soft warning (AIG-FR-024)
    r = await client.post("/api/v1/admin/budgets", json={
        "scope_type": "workspace", "scope_ref": WORKSPACE, "window": "monthly",
        "limit_usd": 150.0, "degrade_pct": 95,
    }, headers=admin_auth())
    assert r.status_code == 201
    assert r.json()["warnings"]
    budget = r.json()["data"]

    # duplicate window → 409
    r = await client.post("/api/v1/admin/budgets", json={
        "scope_type": "workspace", "scope_ref": WORKSPACE, "window": "monthly",
        "limit_usd": 10.0,
    }, headers=admin_auth())
    assert r.status_code == 409

    r = await client.patch(f"/api/v1/admin/budgets/{budget['id']}",
                           json={"limit_usd": 80.0}, headers=admin_auth())
    assert r.json()["data"]["limit_usd"] == 80.0

    r = await client.delete(f"/api/v1/admin/budgets/{budget['id']}",
                            headers=admin_auth())
    assert r.status_code == 200
    r = await client.get(f"/api/v1/admin/budgets/{budget['id']}",
                         headers=admin_auth())
    assert r.status_code == 404


async def test_platform_budget_requires_operator(client, container):
    r = await client.post("/api/v1/admin/budgets", json={
        "scope_type": "platform", "scope_ref": "platform", "window": "monthly",
        "limit_usd": 100000.0,
    }, headers=admin_auth(scopes=["ai.budget.write"]))
    assert r.status_code == 403


async def test_ac12_cross_tenant_budget_404_and_audit(client, container):
    r = await client.post("/api/v1/admin/budgets", json={
        "scope_type": "workspace", "scope_ref": WORKSPACE, "window": "daily",
        "limit_usd": 5.0,
    }, headers=admin_auth(TENANT_A))
    budget_id = r.json()["data"]["id"]

    # tenant B listing shows nothing of tenant A
    r = await client.get("/api/v1/admin/budgets", headers=admin_auth(TENANT_B))
    assert r.json()["data"] == []
    # direct fetch of tenant A's budget → 404 + audit event (MASTER-FR-003)
    r = await client.get(f"/api/v1/admin/budgets/{budget_id}",
                         headers=admin_auth(TENANT_B))
    assert r.status_code == 404
    denied = container.bus.events_of_type("security.cross_tenant_denied")
    assert denied and denied[-1]["payload"]["resource_id"] == budget_id


async def test_spend_endpoint(client, container):
    from tests.conftest import ledger_key_for

    r = await client.post("/api/v1/admin/budgets", json={
        "scope_type": "tenant", "scope_ref": TENANT_A, "window": "monthly",
        "limit_usd": 100.0,
    }, headers=admin_auth())
    budget_id = r.json()["data"]["id"]
    await container.ledger.settle(
        ledger_key_for(budget_id, "monthly", container.clock), "seed", 4200
    )
    r = await client.get(
        f"/api/v1/admin/spend?scope_type=tenant&scope_ref={TENANT_A}&window=monthly",
        headers=admin_auth(),
    )
    assert r.status_code == 200
    row = r.json()["data"][0]
    assert row["spend_usd"] == 42.0
    assert row["limit_usd"] == 100.0
    assert row["reset_at"]


async def test_keys_lifecycle_and_secret_shown_once(client, container):
    r = await client.post("/api/v1/admin/keys", json={
        "principal_type": "user", "principal_id": "user-9", "max_rung": 1,
    }, headers=admin_auth())
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["secret"].startswith("nk-")
    key_id = data["id"]

    r = await client.get("/api/v1/admin/keys", headers=admin_auth())
    assert all("secret" not in k for k in r.json()["data"])

    r = await client.post(f"/api/v1/admin/keys/{key_id}/rotate",
                          headers=admin_auth())
    assert r.json()["data"]["secret"].startswith("nk-")

    r = await client.post(f"/api/v1/admin/keys/{key_id}/revoke",
                          headers=admin_auth())
    assert r.json()["data"]["status"] == "revoked"
    assert container.bus.events_of_type("key.created")
    assert container.bus.events_of_type("key.revoked")


async def test_agent_runtime_mints_key_via_spiffe(client, container):
    """AIG-FR-032: service mint via SPIFFE mTLS identity, no JWT."""
    r = await client.post("/api/v1/admin/keys", json={
        "principal_type": "agent", "principal_id": "analytics",
        "tenant_id": TENANT_A, "ttl_seconds": 3600,
        "allowed_request_classes": ["chat", "sql-gen"],
    }, headers={"x-client-spiffe-id": "spiffe://windrose/ns/ai/sa/agent-runtime"})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["expires_at"] is not None


async def test_unknown_spiffe_rejected(client, container):
    r = await client.post("/api/v1/admin/keys", json={
        "principal_type": "agent", "principal_id": "x", "tenant_id": TENANT_A,
    }, headers={"x-client-spiffe-id": "spiffe://evil/ns/x/sa/y"})
    assert r.status_code == 401


async def test_guardrails_put_pii_off_requires_operator(client, container):
    off_policy = {"policy": {
        "pii": {"mode": "off", "entities": [], "deredact_response": False},
        "injection": {"mode": "block", "flag_threshold": 0.65,
                      "block_threshold": 0.85},
        "schema_validation": "on",
    }}
    r = await client.put("/api/v1/admin/guardrails", json=off_policy,
                         headers=admin_auth(scopes=["ai.guardrail.write"]))
    assert r.status_code == 422  # operator approval flag required
    r = await client.put("/api/v1/admin/guardrails", json=off_policy,
                         headers=admin_auth())  # "*" includes ai.platform.admin
    assert r.status_code == 200
    assert r.json()["data"]["version"] == 1
    assert container.bus.events_of_type("guardrail_policy.updated")


async def test_idempotency_key_replay(client, container):
    headers = {**admin_auth(), "Idempotency-Key": "idem-1"}
    r1 = await client.post("/api/v1/admin/keys", json={
        "principal_type": "user", "principal_id": "u",
    }, headers=headers)
    r2 = await client.post("/api/v1/admin/keys", json={
        "principal_type": "user", "principal_id": "u",
    }, headers=headers)
    assert r1.status_code == r2.status_code == 201
    assert r2.headers.get("idempotency-replayed") == "true"
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]


async def test_admin_requires_auth(client, container):
    r = await client.get("/api/v1/admin/budgets")
    assert r.status_code == 401
