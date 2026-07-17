"""Retention/re-validation AC-8 + policy AC-13 + sessions AC-12 + consumer AC-17."""

from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.errors import ValidationFailed
from app.domain.ports import CallCtx
from app.domain.services import WriteRequest
from tests.conftest import TENANT_A, USER_A, make_settings, prov

pytestmark = pytest.mark.asyncio


def _ctx(tenant=TENANT_A, sub=USER_A):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": sub}, subject=sub)


async def test_ac8_revalidation_decays_and_expires(clock):
    c = build_container(make_settings(), mode="memory", clock=clock)
    ctx = _ctx()
    res = await c.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="stale unretrieved belief",
        provenance=prov("tool_output"), confidence=0.4))
    mid = res.memory_id
    # jump past revalidate_at (50% of 180d)
    clock.advance(days=120)
    out = await c.retention_service.run_revalidation(TENANT_A)
    assert out["decayed"] == 1
    rec = await c.store.get_memory(TENANT_A, mid)
    assert rec.confidence == pytest.approx(0.25)  # 0.4 - 0.15
    # below 0.3 => expired, no longer surfaces
    assert rec.status == "expired"
    results, _ = await c.retrieval_service.retrieve(
        ctx, query_text="stale unretrieved belief", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=None, tags=None,
        snapshot_ver=None, include_debug=False)
    assert results == []


async def test_ac8_recent_retrieval_extends_revalidate(clock):
    c = build_container(make_settings(), mode="memory", clock=clock)
    ctx = _ctx()
    res = await c.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="frequently used preference emea",
        provenance=prov("agent_run", run_id="r"), confidence=0.7))
    # retrieve to set retrieval_count>0
    await c.retrieval_service.retrieve(
        ctx, query_text="frequently used preference emea", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=None, tags=None,
        snapshot_ver=None, include_debug=False)
    before = await c.store.get_memory(TENANT_A, res.memory_id)
    clock.advance(days=120)
    await c.retention_service.run_revalidation(TENANT_A)
    after = await c.store.get_memory(TENANT_A, res.memory_id)
    assert after.confidence == before.confidence  # not decayed
    assert after.revalidate_at > before.revalidate_at


async def test_expiry_job_expires_past_ttl(clock):
    c = build_container(make_settings(ttl_user_default_days=1), mode="memory", clock=clock)
    ctx = _ctx()
    await c.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="short lived memory",
        provenance=prov("agent_run", run_id="r")))
    clock.advance(days=2)
    out = await c.retention_service.run_expiry(TENANT_A)
    assert out["expired"] == 1


async def test_ac13_policy_ttl_bounds(clock):
    c = build_container(make_settings(), mode="memory", clock=clock)
    with pytest.raises(ValidationFailed):
        await c.policy_service.put(TENANT_A, {"ttl_overrides": {"user": "P999D"}})
    # 90 days accepted
    p = await c.policy_service.put(TENANT_A, {"ttl_overrides": {"user": "P90D"}})
    assert p.ttl_overrides["user"] == "P90D"


async def test_ac12_session_wipe_idempotent(container):
    await container.deps.session_store.put(TENANT_A, "sess-x", "e1", {"content": "scratch"})
    assert await container.deps.session_store.list(TENANT_A, "sess-x")
    await container.session_service.wipe(TENANT_A, "sess-x")
    assert await container.deps.session_store.list(TENANT_A, "sess-x") == []
    # second wipe is a no-op (idempotent)
    await container.session_service.wipe(TENANT_A, "sess-x")


async def test_ac17_run_flagged_quarantines_memories(container):
    ctx = _ctx()
    res = await container.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="belief from a poisoned run",
        provenance=prov("agent_run", run_id="run-bad")))
    env = {"event_type": "run.flagged", "tenant_id": TENANT_A, "event_id": "ev-1",
           "payload": {"run_id": "run-bad"}}
    await container.consumer.handle(env)
    rec = await container.store.get_memory(TENANT_A, res.memory_id)
    assert rec.status == "quarantined"
    results, _ = await container.retrieval_service.retrieve(
        ctx, query_text="belief from a poisoned run", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=None, tags=None,
        snapshot_ver=None, include_debug=False)
    assert results == []
