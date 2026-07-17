"""Replay / no-side-effect mode (ART-FR-015): the runtime reproduces the
candidate output an agent WOULD have produced — real graph, captured (not
executed) WriteIntents, snapshot-pinned RAG grounding — WITHOUT creating any
Proposal/Run rows or mutating a case. This is the endpoint eval-service's
AgentRuntimeReplayProvider scores live candidates against."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.fakes import FakeCaseReader, FakeMemory
from app.agents.catalog import seed_catalog
from app.container import build_container
from app.main import create_app
from tests.conftest import TENANT_A, TENANT_B, make_settings, make_token


class _DenyAuthz:
    async def allow(self, **_kw) -> bool:
        return False


@pytest.fixture
async def replay_ctx():
    mem = FakeMemory(results=[{"content": "resolved duplicate-invoice case c-12", "score": 0.9}])
    c = build_container(make_settings(), mode="memory", memory=mem,
                        case_reader=FakeCaseReader())
    await seed_catalog(c.store, c.signing_key)
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, c, mem


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_engine_replay_captures_intent_without_executing():
    """engine.replay returns the disposition + captured WriteIntent and creates
    NO proposal (the graph only EMITS an intent; replay declines to execute it)."""
    mem = FakeMemory(results=[{"content": "prior dup case", "score": 0.8}])
    c = build_container(make_settings(), mode="memory", memory=mem,
                        case_reader=FakeCaseReader())
    outcome = await c.run_engine.replay(
        agent_key="case-triage", inputs={"tenant_id": TENANT_A, "case_id": "c-91"},
        obo_token="tok", prompt_params={}, memory_snapshot_ver="mem_snap_7")

    assert outcome.write_intent is not None  # captured as data
    assert outcome.write_intent.tool_id == "case.apply_disposition"
    assert outcome.structured["severity"] in ("low", "medium", "high", "critical")
    assert outcome.evidence and outcome.evidence[0]["content"] == "prior dup case"
    assert outcome.usage["output_tokens"] > 0  # a real model call happened
    # snapshot pinning reached the memory adapter
    assert mem.calls[-1]["snapshot_ver"] == "mem_snap_7"
    # ZERO side effects: no proposals, no runs persisted
    assert await c.store.list_proposals(TENANT_A) == []
    assert c.store._runs == {}


async def test_replay_endpoint_returns_candidate_output_no_side_effects(replay_ctx):
    client, c, mem = replay_ctx
    tok = make_token(sub="svc:eval-service", tenant_id=TENANT_A, typ="service",
                     scopes=["ai.agent_session.execute"])
    r = await client.post("/api/v1/replay", headers=_auth(tok),
                          json={"agent_key": "case-triage",
                                "candidate": {"content_digest": "sha256:abc"},
                                "input": {"case_id": "c-91"},
                                "memory_snapshot_ver": "mem_snap_3",
                                "no_side_effect": True})
    assert r.status_code == 200, r.text
    out = r.json()["output"]
    # real candidate output (not empty / CANDIDATE_UNAVAILABLE)
    assert out["answer"] and "c-91" in out["answer"]
    assert out["disposition"]["severity"] in ("low", "medium", "high", "critical")
    assert out["evidence"]  # grounding surfaced for the groundedness judge
    # WriteIntent captured-not-executed
    assert len(out["write_intents"]) == 1
    assert out["write_intents"][0]["tool_id"] == "case.apply_disposition"
    assert out["write_intents"][0]["args"]["case_id"] == "c-91"
    assert out["memory_snapshot_ver"] == "mem_snap_3"
    # snapshot pinning honoured
    assert mem.calls[-1]["snapshot_ver"] == "mem_snap_3"
    # ZERO side effects: no proposal rows, no run rows
    assert await c.store.list_proposals(TENANT_A) == []
    assert c.store._runs == {}


async def test_replay_tenant_from_token_not_body(replay_ctx):
    """tenant is ALWAYS the verified token's tenant (BR-11): a TENANT_B token
    replaying cannot read/emit under TENANT_A."""
    client, c, _ = replay_ctx
    tok = make_token(sub="svc:eval-service", tenant_id=TENANT_B, typ="service",
                     scopes=["ai.agent_session.execute"])
    r = await client.post("/api/v1/replay", headers=_auth(tok),
                          json={"agent_key": "case-triage", "input": {"case_id": "c-5"}})
    assert r.status_code == 200, r.text
    # the captured intent's URN is scoped to the token tenant, not any body tenant
    urns = r.json()["output"]["write_intents"][0]["affected_urns"]
    assert urns == [f"wr:{TENANT_B}:case:case/c-5"]


async def test_replay_denied_without_action():
    """A principal lacking ai.agent_session.execute (and denied by OPA) is 403."""
    c = build_container(make_settings(), mode="memory", memory=FakeMemory(),
                        case_reader=FakeCaseReader(), authz=_DenyAuthz())
    await seed_catalog(c.store, c.signing_key)
    app = create_app(c)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        tok = make_token(sub="u-nobody", tenant_id=TENANT_A, scopes=[])
        r = await client.post("/api/v1/replay", headers=_auth(tok),
                              json={"agent_key": "case-triage", "input": {"case_id": "c-1"}})
        assert r.status_code == 403, r.text


async def test_replay_missing_case_id_is_422(replay_ctx):
    client, _, _ = replay_ctx
    tok = make_token(sub="svc:eval-service", tenant_id=TENANT_A, typ="service",
                     scopes=["ai.agent_session.execute"])
    r = await client.post("/api/v1/replay", headers=_auth(tok),
                          json={"agent_key": "case-triage", "input": {}})
    assert r.status_code == 422


async def test_replay_missing_auth_401(replay_ctx):
    client, _, _ = replay_ctx
    r = await client.post("/api/v1/replay",
                          json={"agent_key": "case-triage", "input": {"case_id": "c-1"}})
    assert r.status_code == 401
