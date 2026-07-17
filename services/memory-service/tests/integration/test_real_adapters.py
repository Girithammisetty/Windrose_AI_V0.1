"""Integration tier against real infra:
- AC-1 / AC-4: write with a REAL nomic-embed-text vector, retrieve via pgvector
  ANN, prove the hard tenant filter (cross-tenant returns nothing).
- RLS isolation enforced for the non-privileged memory_rt role.
- AC-7: right-to-erasure cascade verified end-to-end on real Postgres.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.domain.entities import RagChunk
from app.domain.ports import CallCtx
from app.domain.services import WriteRequest
from app.store.schema import tenant_schema
from tests.conftest import TENANT_A, TENANT_B, USER_A, prov

pytestmark = pytest.mark.integration


def _ctx(tenant=TENANT_A, sub=USER_A):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": sub}, subject=sub)


async def test_ac1_ac4_real_embedding_pgvector_ann_and_tenant_filter(real_container):
    """REAL Ollama embeddings + real pgvector ANN. Cross-tenant returns nothing."""
    c = real_container
    ctx = _ctx(TENANT_A)
    res = await c.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A,
        content="user prefers quarterly granularity with an EMEA regional focus",
        provenance=prov("agent_run", run_id="r1", agent_key="analytics")))
    assert res.status == "active"
    rec = await c.store.get_memory(TENANT_A, res.memory_id)
    assert rec.embedding is not None and len(rec.embedding) == 768  # real vector

    # retrieve via real ANN in tenant A
    results, _ = await c.retrieval_service.retrieve(
        ctx, query_text="quarterly EMEA reporting preference", query_embedding=None,
        scopes=[("user", USER_A)], corpora=[], top_k=8, min_confidence=None, tags=None,
        snapshot_ver=None, include_debug=True)
    assert any(r.memory_id == res.memory_id for r in results)

    # AC-4: tenant B issues the same query — hard tenant filter => zero results.
    b_results, _ = await c.retrieval_service.retrieve(
        _ctx(TENANT_B), query_text="quarterly EMEA reporting preference",
        query_embedding=None, scopes=[("user", USER_A)], corpora=[], top_k=8,
        min_confidence=None, tags=None, snapshot_ver=None, include_debug=False)
    assert b_results == []


async def test_rls_isolation_non_privileged_role(container, app_engine):
    """The memory_rt role sees a tenant's rows only when app.tenant_id matches,
    even with search_path pinned to that tenant's schema (RLS, MASTER-FR-001)."""
    ctx = _ctx(TENANT_A)
    await container.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="tenant A private isolation note",
        provenance=prov("agent_run", run_id="r")))
    sch = tenant_schema(TENANT_A)
    async with app_engine.connect() as conn:
        await conn.execute(text(f'SET search_path TO "{sch}", public'))
        # wrong tenant GUC -> RLS hides the rows
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                           {"t": TENANT_B})
        hidden = (await conn.execute(text("SELECT count(*) FROM memories"))).scalar()
        assert hidden == 0
        # correct tenant GUC -> rows visible
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                           {"t": TENANT_A})
        visible = (await conn.execute(text("SELECT count(*) FROM memories"))).scalar()
        assert visible == 1


async def test_ac7_erasure_cascade_end_to_end(container):
    c = container
    ctx = _ctx(TENANT_A)
    await c.write_service.write(ctx, WriteRequest(
        scope="user", scope_ref=USER_A, content="alice prefers EMEA invoices",
        provenance=prov("agent_run", run_id="r", user_id=USER_A)))
    await c.write_service.write(ctx, WriteRequest(
        scope="workspace", scope_ref="ws-1", content="workspace note authored by alice",
        provenance=prov("agent_run", run_id="r2", user_id=USER_A)))
    await c.store.upsert_chunk(RagChunk(
        chunk_id="00000000-0000-7000-8000-0000000000c1", tenant_id=TENANT_A,
        corpus_key="resolved_cases", source_urn="wr:t:case:case/1", chunk_seq=0,
        content="case comment by alice", embedding=[0.05] * 768,
        embedding_model_ver="v1", snapshot_ver="2026-07-08", source_updated_at=None,
        user_linkage=USER_A))

    from app.domain.entities import ErasureRequest
    from app.utils import new_id
    req = ErasureRequest(request_id=new_id(), tenant_id=TENANT_A, subject_type="user",
                         subject_id=USER_A, status="received", created_at=c.clock.now())
    await c.store.add_erasure(req)
    final = await c.erasure_service.run_sync(ctx, req)

    assert final.status == "completed"
    counts = final.report["counts_deleted"]
    assert counts["user_scope_memories"] == 1
    assert counts["provenance_linked_memories"] == 1
    assert counts["rag_chunks"] == 1
    assert all(v == 0 for v in final.report["verification_queries"].values())
    # verify in the DB directly: nothing left for the subject
    assert await c.store.count_active(TENANT_A, "user", USER_A) == 0
    assert await c.store.count_chunks_by_user(TENANT_A, USER_A) == 0
