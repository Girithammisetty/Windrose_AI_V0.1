"""Right-to-erasure AC-7: cascade across scopes + chunks + sessions, verified
report with per-store counts."""

from __future__ import annotations

import pytest

from app.domain.entities import RagChunk
from app.domain.ports import CallCtx
from app.domain.services import WriteRequest
from tests.conftest import TENANT_A, USER_A, prov

pytestmark = pytest.mark.asyncio


def _ctx(tenant=TENANT_A, sub=USER_A):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": sub}, subject=sub)


async def test_ac7_erasure_cascade_and_report(container):
    ctx = _ctx()
    # user-scope memory
    await container.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="alice prefers EMEA invoices",
        provenance=prov("agent_run", run_id="r", user_id=USER_A)))
    # workspace-scope memory whose provenance references the subject
    await container.write_service.write(ctx, WriteRequest(
        scope="workspace", scope_ref="ws-1",
        content="workspace note authored by alice about triage",
        provenance=prov("agent_run", run_id="r2", user_id=USER_A)))
    # a RAG chunk attributable to the user
    await container.store.upsert_chunk(RagChunk(
        chunk_id="c1", tenant_id=TENANT_A, corpus_key="resolved_cases",
        source_urn="wr:t:case:case/1", chunk_seq=0, content="case comment by alice",
        embedding=[0.1] * 768, embedding_model_ver="v1", snapshot_ver="2026-07-08",
        source_updated_at=None, user_linkage=USER_A))
    # a live session referencing the subject
    await container.deps.session_store.put(
        TENANT_A, "sess-1", "e1", {"content": f"note about {USER_A}"})

    req = await container.erasure_service.start(ctx, "user", USER_A)
    # start schedules an async task; run synchronously for a deterministic assert
    final = await container.deps.store.get_erasure(TENANT_A, req.request_id)
    # ensure the background task completed
    import asyncio
    for _ in range(50):
        final = await container.deps.store.get_erasure(TENANT_A, req.request_id)
        if final and final.status in ("completed", "failed"):
            break
        await asyncio.sleep(0.01)

    assert final.status == "completed"
    report = final.report
    assert report["verified"] is True
    counts = report["counts_deleted"]
    assert counts["user_scope_memories"] == 1
    assert counts["provenance_linked_memories"] == 1
    assert counts["rag_chunks"] == 1
    # verification probes all zero
    assert all(v == 0 for v in report["verification_queries"].values())
    assert report["subject_digest"]  # signed digest present
    assert any(e["event_type"] == "erasure.completed" for _, e in container.store.outbox)


async def test_erasure_removes_from_retrieval(container):
    ctx = _ctx()
    await container.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="alice secret preference token",
        provenance=prov("agent_run", run_id="r", user_id=USER_A)))
    req = await container.erasure_service.start(ctx, "user", USER_A)
    import asyncio
    for _ in range(50):
        f = await container.deps.store.get_erasure(TENANT_A, req.request_id)
        if f and f.status in ("completed", "failed"):
            break
        await asyncio.sleep(0.01)
    results, _ = await container.retrieval_service.retrieve(
        ctx, query_text="alice secret preference", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=None, tags=None,
        snapshot_ver=None, include_debug=False)
    assert results == []
