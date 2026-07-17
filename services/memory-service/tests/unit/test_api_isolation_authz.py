"""API-level tests: envelope, auth, isolation, authz matrix, session hook."""

from __future__ import annotations

import pytest

from tests.conftest import TENANT_A, TENANT_B, USER_A, auth, prov

pytestmark = pytest.mark.asyncio


async def test_missing_bearer_401(client):
    r = await client.post("/api/v1/retrieve", json={"query_text": "x", "scopes": []})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_write_and_browse_envelope(client):
    r = await client.post("/api/v1/memories", json={
        "scope": "user", "scope_ref": USER_A, "content": "browse me later please",
        "provenance": prov("user_explicit", user_id=USER_A)}, headers=auth(sub=USER_A))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "active"
    r2 = await client.get("/api/v1/memories?scope=user", headers=auth(sub=USER_A))
    assert r2.status_code == 200
    body = r2.json()
    assert "data" in body and "page" in body
    assert any(m["content"] == "browse me later please" for m in body["data"])


async def test_ac4_cross_tenant_retrieval_isolation(client):
    await client.post("/api/v1/memories", json={
        "scope": "user", "scope_ref": USER_A, "content": "tenant A only invoice secret",
        "provenance": prov("agent_run", run_id="r")}, headers=auth(TENANT_A, USER_A))
    # Tenant B, same user id and query -> zero results (hard tenant filter).
    r = await client.post("/api/v1/retrieve", json={
        "query_text": "tenant A only invoice secret", "scopes": ["user"]},
        headers=auth(TENANT_B, USER_A))
    assert r.status_code == 200
    assert r.json()["data"] == []


async def test_authz_matrix_write_denied_without_scope(client):
    # user token limited to read scope cannot write
    r = await client.post("/api/v1/memories", json={
        "scope": "user", "scope_ref": USER_A, "content": "should be denied",
        "provenance": prov("user_explicit", user_id=USER_A)},
        headers=auth(sub=USER_A, scopes=["memory.memory.read"]))
    assert r.status_code == 403
    assert r.json()["error"]["code"] in ("PERMISSION_DENIED",)


async def test_user_cannot_write_other_users_scope(client):
    r = await client.post("/api/v1/memories", json={
        "scope": "user", "scope_ref": "someone-else", "content": "not my scope",
        "provenance": prov("user_explicit", user_id=USER_A)},
        headers=auth(sub=USER_A, scopes=["memory.memory.create"]))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "SCOPE_DENIED"


async def test_session_hook_requires_spiffe(client):
    r = await client.delete(f"/internal/v1/sessions/sess-1/memory?tenant={TENANT_A}")
    assert r.status_code == 403


async def test_session_hook_idempotent_204(client):
    headers = {"x-client-spiffe-id": "spiffe://windrose/ns/ai/sa/agent-runtime"}
    r1 = await client.delete(
        f"/internal/v1/sessions/sess-1/memory?tenant={TENANT_A}", headers=headers)
    assert r1.status_code == 204
    r2 = await client.delete(
        f"/internal/v1/sessions/sess-1/memory?tenant={TENANT_A}", headers=headers)
    assert r2.status_code == 204


async def test_erasure_endpoint_returns_operation_id(client):
    r = await client.post("/api/v1/erasure", json={"subject_type": "user",
                          "subject_id": USER_A}, headers=auth(sub="dpo-1"))
    assert r.status_code == 202
    assert "operation_id" in r.json()["data"]
