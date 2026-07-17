"""End-to-end (unit tier) answer-delivery contract: the chat answer must be
(1) streamed to the hub topic as token -> run_completed -> done,
(2) persisted on the Run (GET /runs/{id} returns final_text), and
(3) the session-ownership key projected for hub chat authz (FIX 2/4).
Plus the bff proposals contract: filter[resource_urn] + per-proposal
resource_urn (FIX 7)."""

from __future__ import annotations

import httpx
import pytest

from app.agents.catalog import seed_catalog
from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, make_settings, make_token


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


async def test_answer_is_streamed_and_persisted(client_and_container):
    client, c = client_and_container
    tok = make_token(sub="u-77", tenant_id=TENANT_A)
    r = await client.post("/api/v1/agents/analytics/chat/completions",
                          headers=_auth(tok),
                          json={"messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    run_id = body["run_id"]
    assert body["final_text"]  # inline summary carries the answer

    # (b) hub stream: token (full text as one chunk) -> run_completed -> done,
    # on the topic the chat response advertised, with tenant_id for the hub.
    topic = r.headers["x-windrose-stream-topic"]
    events = [e for e in c.realtime.events if e["topic"] == topic]
    kinds = [e["event"] for e in events]
    assert kinds == ["token", "run_completed", "done"]
    token_ev, completed_ev, done_ev = events
    assert token_ev["data"]["text"] == body["final_text"]
    assert token_ev["tenant_id"] == TENANT_A
    assert completed_ev["data"]["final_text"] == body["final_text"]

    # (a)+(c) persisted + readable by non-streaming clients.
    r = await client.get(f"/api/v1/runs/{run_id}", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["data"]["final_text"] == body["final_text"]
    assert r.json()["data"]["status"] == "completed"


async def test_session_ownership_key_projected_for_hub_authz(client_and_container):
    client, c = client_and_container
    tok = make_token(sub="u-77", tenant_id=TENANT_A)
    r = await client.post("/api/v1/agents/analytics/chat/completions",
                          headers=_auth(tok),
                          json={"messages": [{"role": "user", "content": "hi"}]})
    session_id = r.json()["data"]["session_id"]
    key = f"rt:session:{TENANT_A}/{session_id}"
    assert key in c.session_proj.keys
    owner, ttl = c.session_proj.keys[key]
    assert owner == "u-77"
    assert 0 < ttl <= c.settings.max_lifetime_seconds

    # resuming the session refreshes the projection
    c.session_proj.keys.clear()
    r = await client.post("/api/v1/agents/analytics/chat/completions",
                          headers=_auth(tok),
                          json={"messages": [{"role": "user", "content": "again"}],
                                "metadata": {"session_id": session_id}})
    assert r.status_code == 200
    assert key in c.session_proj.keys


async def test_proposals_filter_by_resource_urn_and_view_field(client_and_container):
    client, c = client_and_container
    tok = make_token(sub="u-77", tenant_id=TENANT_A, scopes=["tenant.admin"])
    r = await client.post("/api/v1/agents/case-triage/chat/completions",
                          headers=_auth(tok),
                          json={"messages": [{"role": "user", "content": "triage"}],
                                "metadata": {"case_id": "c-91"}})
    pid = r.json()["data"]["proposal_id"]
    case_urn = f"wr:{TENANT_A}:case:case/c-91"

    # bff sends filter[resource_urn]=<urn>[,<urn>] and reads resource_urn back.
    r = await client.get(
        "/api/v1/proposals",
        params={"filter[resource_urn]": f"{case_urn},wr:{TENANT_A}:case:case/other"},
        headers=_auth(tok))
    assert r.status_code == 200
    rows = r.json()["data"]
    assert any(p["id"] == pid for p in rows)
    match = next(p for p in rows if p["id"] == pid)
    assert match["resource_urn"] == case_urn

    # a non-matching URN filters everything out
    r = await client.get("/api/v1/proposals",
                         params={"filter[resource_urn]": f"wr:{TENANT_A}:case:case/nope"},
                         headers=_auth(tok))
    assert r.json()["data"] == []
