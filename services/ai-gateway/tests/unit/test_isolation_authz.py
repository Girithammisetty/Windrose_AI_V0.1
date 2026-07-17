"""Tenant-isolation suite (MASTER-FR-004, unit variant with the in-memory
tenant-policy fake) + authz matrix (MASTER-FR-071): every admin endpoint ×
missing scope → 403, tenant A token against tenant B resources → 404/empty."""

from __future__ import annotations

import pytest

from tests.conftest import (
    TENANT_A,
    TENANT_B,
    WORKSPACE,
    admin_auth,
    dp_headers,
    mint_key,
    seed_default_deployments,
)

# (method, path_template, body, required_scope+operator?)
ADMIN_MATRIX = [
    ("GET", "/api/v1/admin/providers", None, True),
    ("POST", "/api/v1/admin/providers", {
        "provider": "bedrock", "model_family": "fast-small",
        "deployment_name": "d", "region": "r", "cloud": "aws",
        "endpoint_vault_ref": "v"}, True),
    ("GET", "/api/v1/admin/ladders/chat", None, False),
    ("PUT", "/api/v1/admin/ladders/chat", {
        "rungs": [{"model_alias": "fast-small", "max_tokens": 10,
                   "temperature_default": 0.1, "cost_tier": 1}],
        "scope": "tenant"}, False),
    ("GET", "/api/v1/admin/budgets", None, False),
    ("POST", "/api/v1/admin/budgets", {
        "scope_type": "workspace", "scope_ref": "ws", "window": "daily",
        "limit_usd": 1.0}, False),
    ("GET", f"/api/v1/admin/spend?scope_type=tenant&scope_ref={TENANT_A}",
     None, False),
    ("GET", "/api/v1/admin/keys", None, False),
    ("POST", "/api/v1/admin/keys",
     {"principal_type": "user", "principal_id": "u"}, False),
    ("GET", "/api/v1/admin/guardrails", None, False),
    ("PUT", "/api/v1/admin/guardrails", {"policy": {
        "pii": {"mode": "redact", "entities": ["EMAIL"]},
        "injection": {"mode": "block"}, "schema_validation": "on"}}, False),
    ("DELETE", "/api/v1/admin/cache?scope=tenant", None, False),
]


@pytest.mark.parametrize("method,path,body,_operator", ADMIN_MATRIX)
async def test_authz_matrix_no_scopes_is_403(client, method, path, body, _operator):
    headers = admin_auth(scopes=[])  # authenticated, zero grants
    r = await client.request(method, path, json=body, headers=headers)
    assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
    assert r.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("method,path,body,_operator", ADMIN_MATRIX)
async def test_authz_matrix_unauthenticated_is_401(client, method, path, body,
                                                   _operator):
    r = await client.request(method, path, json=body)
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


async def test_isolation_budgets_keys_between_tenants(client, container):
    # tenant A creates a budget and a key
    rb = await client.post("/api/v1/admin/budgets", json={
        "scope_type": "workspace", "scope_ref": WORKSPACE, "window": "daily",
        "limit_usd": 9.0}, headers=admin_auth(TENANT_A))
    budget_id = rb.json()["data"]["id"]
    rk = await client.post("/api/v1/admin/keys", json={
        "principal_type": "user", "principal_id": "ua"},
        headers=admin_auth(TENANT_A))
    key_id = rk.json()["data"]["id"]

    # tenant B sees neither
    r = await client.get("/api/v1/admin/budgets", headers=admin_auth(TENANT_B))
    assert r.json()["data"] == []
    r = await client.get("/api/v1/admin/keys", headers=admin_auth(TENANT_B))
    assert r.json()["data"] == []
    # direct id access → 404, and revoke of A's key from B → 404
    r = await client.get(f"/api/v1/admin/budgets/{budget_id}",
                         headers=admin_auth(TENANT_B))
    assert r.status_code == 404
    r = await client.post(f"/api/v1/admin/keys/{key_id}/revoke",
                          headers=admin_auth(TENANT_B))
    assert r.status_code == 404


async def test_isolation_guardrail_policy_between_tenants(client, container):
    await client.put("/api/v1/admin/guardrails", json={"policy": {
        "pii": {"mode": "block", "entities": ["EMAIL"]},
        "injection": {"mode": "block"}, "schema_validation": "on"}},
        headers=admin_auth(TENANT_A))
    r = await client.get("/api/v1/admin/guardrails", headers=admin_auth(TENANT_B))
    assert r.json()["data"]["policy"]["pii"]["mode"] == "redact"  # B still default


async def test_isolation_tenant_ladder_override_not_visible_to_others(
        client, container):
    rungs = [{"model_alias": "fast-small", "max_tokens": 10,
              "temperature_default": 0.1, "cost_tier": 1}]
    await client.put("/api/v1/admin/ladders/chat",
                     json={"rungs": rungs, "scope": "tenant"},
                     headers=admin_auth(TENANT_A))
    r = await client.get("/api/v1/admin/ladders/chat", headers=admin_auth(TENANT_B))
    assert len(r.json()["data"]["rungs"]) == 3  # B sees the platform default


async def test_isolation_data_plane_spend_attribution(client, container):
    """Requests from A never touch B's budget windows."""
    await seed_default_deployments(container)
    _, secret_a = await mint_key(container, TENANT_A)
    from tests.conftest import CHAT_BODY, ledger_key_for

    await client.post("/v1/chat/completions", json=CHAT_BODY,
                      headers=dp_headers(secret_a, TENANT_A))
    b_key = ledger_key_for(f"default-{TENANT_B}-daily", "daily", container.clock)
    assert await container.ledger.usage(b_key) == (0, 0)
