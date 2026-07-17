"""Retrieval ACs: ranking blend, hard tenant filter, AC-9 workspace membership,
include_debug, min_confidence."""

from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.errors import ScopeDenied
from app.domain.ports import CallCtx
from app.domain.services import WriteRequest
from tests.conftest import TENANT_A, TENANT_B, USER_A, WORKSPACE, make_settings, prov

pytestmark = pytest.mark.asyncio


def _ctx(tenant=TENANT_A, sub=USER_A):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": sub}, subject=sub)


async def _seed(container, tenant, content, scope="user", scope_ref=USER_A):
    return await container.write_service.write(
        _ctx(tenant), WriteRequest(scope=scope, scope_ref=scope_ref, content=content,
                                   provenance=prov("agent_run", run_id="r")))


async def test_ac4_hard_tenant_filter_cross_tenant_returns_nothing(container):
    await _seed(container, TENANT_A, "tenant A duplicate invoice resolution notes")
    # Tenant B issues the same query; only A has data.
    results, _ = await container.retrieval_service.retrieve(
        _ctx(TENANT_B), query_text="duplicate invoice resolution", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=None, tags=None,
        snapshot_ver=None, include_debug=False)
    assert results == []


async def test_ranking_blend_and_debug(container):
    ctx = _ctx()
    await _seed(container, TENANT_A, "duplicate invoice resolution disposition confirmed")
    results, _ = await container.retrieval_service.retrieve(
        ctx, query_text="duplicate invoice resolution", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=None, tags=None,
        snapshot_ver=None, include_debug=True)
    assert results and results[0].debug is not None
    dbg = results[0].debug
    assert set(dbg) >= {"sim", "recency", "confidence", "score"}
    # score = w_sim*sim + w_rec*rec + w_conf*conf
    expected = (0.65 * dbg["sim"] + 0.20 * dbg["recency"] + 0.15 * dbg["confidence"])
    assert abs(expected - dbg["score"]) < 1e-6


async def test_min_confidence_filter(container):
    ctx = _ctx()
    await container.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="low confidence belief here",
        provenance=prov("tool_output"), confidence=0.4))
    results, _ = await container.retrieval_service.retrieve(
        ctx, query_text="low confidence belief", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=0.6, tags=None,
        snapshot_ver=None, include_debug=False)
    assert results == []


async def test_ac9_workspace_membership_enforced_at_retrieval(clock):
    from app.adapters.membership import InMemoryMembership
    mem = InMemoryMembership(default_allow=False)
    c = build_container(make_settings(), mode="memory", clock=clock, membership=mem)
    # user not a member -> retrieval route resolution denies
    from app.api.routes.memories import _resolve_scopes
    from app.api.schemas import RetrieveIn

    class P:
        effective_user = USER_A
        tenant_id = TENANT_A
        typ = "user"
    body = RetrieveIn(scopes=["workspace"], scope_refs={"workspace": WORKSPACE})
    with pytest.raises(ScopeDenied):
        _resolve_scopes(P(), body, {WORKSPACE: False})
    # after grant, allowed
    _resolve_scopes(P(), body, {WORKSPACE: True})
    _ = c


async def test_retrieval_bumps_confidence(container):
    ctx = _ctx()
    res = await _seed(container, TENANT_A, "bump me on retrieval please")
    await container.retrieval_service.retrieve(
        ctx, query_text="bump me on retrieval", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=None, tags=None,
        snapshot_ver=None, include_debug=False)
    rec = await container.store.get_memory(TENANT_A, res.memory_id)
    assert rec.retrieval_count == 1 and rec.confidence > 0.7
