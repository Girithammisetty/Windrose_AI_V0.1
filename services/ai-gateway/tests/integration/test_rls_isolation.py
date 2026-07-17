"""RLS-backed tenant isolation (MASTER-FR-001/003/004) against real Postgres
with a non-privileged role, plus the keyauth policy for virtual keys."""

from __future__ import annotations

import pytest

from tests.conftest import (
    CHAT_BODY,
    TENANT_A,
    TENANT_B,
    WORKSPACE,
    admin_auth,
    dp_headers,
    mint_key,
    seed_default_deployments,
)

pytestmark = pytest.mark.integration


async def test_rls_hides_cross_tenant_budgets(client, container):
    r = await client.post("/api/v1/admin/budgets", json={
        "scope_type": "workspace", "scope_ref": WORKSPACE, "window": "daily",
        "limit_usd": 9.0}, headers=admin_auth(TENANT_A))
    assert r.status_code == 201, r.text
    budget_id = r.json()["data"]["id"]

    # direct repo access under tenant B's RLS context sees nothing
    async with container.uow_factory(TENANT_B) as uow:
        assert await uow.budgets.get(budget_id) is None
        page = await uow.budgets.list(50, None)
        assert page.data == []
    # API surface: 404 for direct id fetch
    r = await client.get(f"/api/v1/admin/budgets/{budget_id}",
                         headers=admin_auth(TENANT_B))
    assert r.status_code == 404
    r = await client.get(f"/api/v1/admin/budgets/{budget_id}",
                         headers=admin_auth(TENANT_A))
    assert r.status_code == 200


async def test_keyauth_policy_allows_hash_lookup_only(client, container):
    key, secret = await mint_key(container, TENANT_A)
    # tenant B's RLS context cannot see the key by id...
    async with container.uow_factory(TENANT_B) as uow:
        assert await uow.keys.get(key.id) is None
    # ...but the authenticator (keyauth GUC) resolves the hash cross-tenant
    resolved = await container.key_service.authenticate(secret)
    assert resolved.id == key.id and resolved.tenant_id == TENANT_A


async def test_data_plane_end_to_end_on_postgres_and_redis(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container, TENANT_A)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200, r.text
    assert r.headers["x-windrose-rung"] == "0"
    # request logged under tenant A only
    request_id = r.headers["x-windrose-request-id"]
    async with container.uow_factory(TENANT_A) as uow:
        assert (await uow.request_log.get(request_id)) is not None
    async with container.uow_factory(TENANT_B) as uow:
        assert (await uow.request_log.get(request_id)) is None


async def test_guardrail_policy_versioning_rows(client, container):
    for mode in ("block", "flag"):
        r = await client.put("/api/v1/admin/guardrails", json={"policy": {
            "pii": {"mode": "redact", "entities": ["EMAIL"]},
            "injection": {"mode": mode, "flag_threshold": 0.65,
                          "block_threshold": 0.85},
            "schema_validation": "on"}}, headers=admin_auth(TENANT_A))
        assert r.status_code == 200
    assert r.json()["data"]["version"] == 2
    async with container.uow_factory(TENANT_A) as uow:
        current = await uow.policies.current()
    assert current.version == 2
    assert current.policy["injection"]["mode"] == "flag"
