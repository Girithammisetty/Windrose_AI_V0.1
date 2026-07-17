"""Corpus ACs: AC-6 (case.resolved ingest + anonymization), AC-10 (rebuild
atomic version switch), AC-14 (snapshot pin), docs push, tombstone."""

from __future__ import annotations

import pytest

from app.domain.ports import CallCtx
from tests.conftest import TENANT_A, prov

pytestmark = pytest.mark.asyncio


def _ctx(tenant=TENANT_A, sub="op-1"):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": sub}, subject=sub)


async def _provision(container):
    await container.provisioning.provision(TENANT_A)


async def test_ac6_case_resolved_ingest_anonymized_and_keyed(container):
    await _provision(container)
    env = {
        "event_type": "case.resolved", "tenant_id": TENANT_A,
        "resource_urn": "wr:t:case:case/c-7f1",
        "payload": {
            "resolution_narrative": "Resolved by Mr. John Smith email john@acme.com as duplicate",
            "disposition": "confirmed", "evidence_summary": "vendor entry twice",
            "case_type": "duplicate_invoice", "authored_by": "user-carol",
        },
    }
    n = await container.corpus_service.ingest_event(TENANT_A, env)
    assert n >= 1
    chunks = await container.store.list_chunks(TENANT_A, "resolved_cases")
    assert chunks and all(c.source_urn == "wr:t:case:case/c-7f1" for c in chunks)
    joined = " ".join(c.content for c in chunks)
    assert "John Smith" not in joined and "john@acme.com" not in joined
    assert "[PERSON]" in joined or "[EMAIL]" in joined  # anonymized before embed

    # re-ingest (edit) replaces, does not duplicate
    n2 = await container.corpus_service.ingest_event(TENANT_A, env)
    chunks2 = await container.store.list_chunks(TENANT_A, "resolved_cases")
    assert len(chunks2) == n2  # replaced, not accumulated


async def test_ac10_rebuild_atomic_version_switch(container):
    await _provision(container)
    env = {"event_type": "case.resolved", "tenant_id": TENANT_A,
           "resource_urn": "wr:t:case:case/c-1",
           "payload": {"resolution_narrative": "some resolution text here to chunk",
                       "disposition": "confirmed"}}
    await container.corpus_service.ingest_event(TENANT_A, env)
    before = await container.store.list_chunks(TENANT_A, "resolved_cases")
    old_ver = before[0].embedding_model_ver

    report = await container.corpus_service.rebuild(_ctx(), "resolved_cases", "nomic-embed-text/v2")
    assert report["active_embedding_ver"] == "nomic-embed-text/v2"
    after = await container.store.list_chunks(TENANT_A, "resolved_cases")
    # no mixed versions: every remaining chunk is on the new version
    assert all(c.embedding_model_ver == "nomic-embed-text/v2" for c in after)
    assert all(c.embedding_model_ver != old_ver for c in after)
    assert report["old_chunks_dropped"] == len(before)


async def test_ac14_snapshot_pin(container):
    await _provision(container)
    # ingest at snapshot date driven by the fake clock (2026-07-09)
    env = {"event_type": "case.resolved", "tenant_id": TENANT_A,
           "resource_urn": "wr:t:case:case/snap",
           "payload": {"resolution_narrative": "snapshot pinned resolution content",
                       "disposition": "confirmed"}}
    await container.corpus_service.ingest_event(TENANT_A, env)
    ctx = _ctx()
    emb = await container.deps.embedder.embed(TENANT_A, "snapshot pinned resolution")
    # pin to an older snapshot -> nothing (chunk snapshot 2026-07-09 > 2026-07-01)
    old, _ = await container.retrieval_service.retrieve(
        ctx, query_text=None, query_embedding=emb, scopes=[], corpora=["resolved_cases"],
        top_k=8, min_confidence=None, tags=None, snapshot_ver="2026-07-01",
        include_debug=False)
    assert old == []
    # pin to the current snapshot -> returns
    cur, _ = await container.retrieval_service.retrieve(
        ctx, query_text=None, query_embedding=emb, scopes=[], corpora=["resolved_cases"],
        top_k=8, min_confidence=None, tags=None, snapshot_ver="2026-07-09",
        include_debug=False)
    assert cur and cur[0].corpus == "resolved_cases"


async def test_docs_push(container):
    await _provision(container)
    n = await container.corpus_service.add_document(
        _ctx(), "wr:t:doc:doc/runbook-1",
        "Runbook: to drain the DLQ run rpk and reset the consumer group offsets.")
    assert n >= 1
    assert await container.store.count_chunks(TENANT_A, "docs") == n


async def test_schemas_corpus_from_dataset_event(container):
    await _provision(container)
    env = {"event_type": "dataset.profiled", "tenant_id": TENANT_A,
           "resource_urn": "wr:t:dataset:dataset/ds-1",
           "payload": {"name": "orders", "description": "sales orders",
                       "columns": [{"name": "id", "type": "bigint"}],
                       "profile": {"row_count": 100, "distinct_count": 90}}}
    n = await container.corpus_service.ingest_event(TENANT_A, env)
    assert n >= 1
    _ = prov  # keep import used
