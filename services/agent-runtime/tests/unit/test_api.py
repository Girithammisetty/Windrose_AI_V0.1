"""API-level tests over the ASGI app (memory container): the triage chat path
creates a proposal, the inbox lists it, decide is idempotent, and tenant isolation
returns 404 (AC-14, BR-11)."""

from __future__ import annotations

import httpx
import pytest

from app.agents.catalog import seed_catalog
from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, make_settings, make_token


@pytest.fixture
async def client_and_container():
    c = build_container(make_settings(), mode="memory")
    await seed_catalog(c.store, c.signing_key)
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_jwks_served(client_and_container):
    client, c = client_and_container
    r = await client.get("/api/v1/.well-known/jwks.json")
    assert r.status_code == 200
    assert r.json()["keys"][0]["kid"] == c.signing_key.kid


async def test_triage_chat_creates_proposal_then_inbox_and_decide(client_and_container):
    client, c = client_and_container
    tok = make_token(sub="u-77", tenant_id=TENANT_A, scopes=["tenant.admin"])
    r = await client.post("/api/v1/agents/case-triage/chat/completions",
                          headers=_auth(tok),
                          json={"messages": [{"role": "user", "content": "triage this"}],
                                "metadata": {"case_id": "c-91"}})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["proposal_status"] == "pending"
    assert r.headers["x-windrose-ai-generated"] == "true"
    assert r.headers["x-windrose-stream-topic"].startswith("agent_run:")
    pid = body["proposal_id"]

    # inbox
    r = await client.get("/api/v1/proposals?filter[status]=pending", headers=_auth(tok))
    assert r.status_code == 200
    assert any(p["id"] == pid for p in r.json()["data"])

    # approve (approver is a different user; allow-all authz in unit tier)
    approver = make_token(sub="u-super", tenant_id=TENANT_A, scopes=["tenant.admin"])
    r = await client.post(f"/api/v1/proposals/{pid}/decide", headers=_auth(approver),
                          json={"action": "approve"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "approved"

    # second decide -> 409 CONFLICT (first wins)
    r = await client.post(f"/api/v1/proposals/{pid}/decide", headers=_auth(approver),
                          json={"action": "reject"})
    assert r.status_code == 409


async def test_cross_tenant_proposal_is_404(client_and_container):
    client, c = client_and_container
    tok_a = make_token(sub="u-77", tenant_id=TENANT_A)
    r = await client.post("/api/v1/agents/case-triage/chat/completions", headers=_auth(tok_a),
                          json={"messages": [{"role": "user", "content": "x"}],
                                "metadata": {"case_id": "c-91"}})
    pid = r.json()["data"]["proposal_id"]
    tok_b = make_token(sub="u-x", tenant_id=TENANT_B)
    r = await client.get(f"/api/v1/proposals/{pid}", headers=_auth(tok_b))
    assert r.status_code == 404


async def test_missing_auth_401(client_and_container):
    client, _ = client_and_container
    r = await client.get("/api/v1/proposals")
    assert r.status_code == 401


async def test_destructive_auto_policy_rejected_422(client_and_container):
    client, _ = client_and_container
    tok = make_token(sub="admin", tenant_id=TENANT_A, scopes=["tenant.admin"])
    r = await client.put("/api/v1/registry/tenants/self/agents/case-triage", headers=_auth(tok),
                         json={"auto_execute_policy":
                               {"case-triage": {"write-proposal": {"destructive": "auto"}}}})
    assert r.status_code == 422
