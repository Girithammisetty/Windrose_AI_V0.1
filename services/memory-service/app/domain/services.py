"""Domain services: write pipeline, retrieval, sessions, corpora, erasure,
retention, policy, admin, provisioning. Operates over the store-agnostic port
so the same logic runs on pgvector (integration/prod) and the in-memory unit
double."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain import corpus_mappers as cm
from app.domain import policy as pol
from app.domain.chunking import chunk_text
from app.domain.entities import (
    ALL_SCOPES,
    DURABLE_SCOPES,
    SCOPE_SESSION,
    SCOPE_USER,
    SCOPE_WORKSPACE,
    SRC_ADMIN,
    STATUS_ACTIVE,
    STATUS_QUARANTINED,
    Corpus,
    ErasureRequest,
    MemoryRecord,
    Provenance,
    RagChunk,
    ScoredResult,
    TenantPolicy,
)
from app.domain.errors import (
    Conflict,
    EmbeddingUnavailable,
    NotFound,
    PiiRejected,
    ScopeDenied,
    ValidationFailed,
)
from app.domain.ports import CallCtx, ServiceDeps
from app.domain.ranking import blend
from app.domain.urn import corpus_urn, erasure_urn, memory_urn
from app.events.envelope import make_envelope
from app.utils import json_size_bytes, new_id

TOPIC = "memory.events.v1"


async def ensure_tenant_provisioned(deps: ServiceDeps, tenant_id: str) -> None:
    """Lazy ensure-on-first-use provisioning (BR-14 fallback).

    The event-driven path (identity ``tenant.provisioned`` -> consumer ->
    ``ProvisioningService.provision``) only covers tenants created while the
    consumers are running. Tenants provisioned before that (or whose event was
    missed) have no ``mem_t_<tenant>`` schema, so any memories/rag_chunks query
    would 500. Retrieve/write/consumer entry points call this instead: it
    checks the store once per tenant per process (cached in
    ``deps.provisioned_tenants``) and runs the full idempotent provisioning
    (schema DDL + default policy + standard corpora) when missing.
    """
    if not tenant_id or tenant_id in deps.provisioned_tenants:
        return
    if not await deps.store.tenant_ready(tenant_id):
        await ProvisioningService(deps).provision(tenant_id)
    deps.provisioned_tenants.add(tenant_id)


@dataclass
class WriteRequest:
    scope: str
    scope_ref: str
    content: str
    provenance: dict
    confidence: float | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class WriteResult:
    memory_id: str | None
    status: str  # active|quarantined|merged
    merged: bool = False
    degraded: bool = False
    session: bool = False


# --------------------------------------------------------------------------- #
# Write path (MEM-FR-010..013)                                                 #
# --------------------------------------------------------------------------- #
class WriteService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps
        self.s = deps.settings

    async def _policy(self, tenant_id: str) -> TenantPolicy:
        p = await self.d.store.get_policy(tenant_id)
        return p or TenantPolicy(tenant_id=tenant_id)

    def _validate(self, req: WriteRequest) -> None:
        if not req.content or not req.content.strip():
            raise ValidationFailed("content is required")
        if json_size_bytes(req.content) > self.s.content_max_bytes:
            raise ValidationFailed(f"content exceeds {self.s.content_max_bytes} bytes")
        if len(req.tags) > self.s.max_tags:
            raise ValidationFailed(f"at most {self.s.max_tags} tags")
        if req.scope not in ALL_SCOPES:
            raise ValidationFailed(f"unknown scope {req.scope!r}")
        st = req.provenance.get("source_type")
        if st not in ("agent_run", "user_explicit", "tool_output", "admin"):
            raise ValidationFailed("provenance.source_type invalid")

    async def write(self, ctx: CallCtx, req: WriteRequest) -> WriteResult:
        self._validate(req)
        # Session scope: Redis only, no embed/screen/pgvector (BR-3).
        if req.scope == SCOPE_SESSION:
            await self.d.session_store.put(
                ctx.tenant_id, req.scope_ref, new_id(),
                {"content": req.content, "provenance": req.provenance,
                 "tags": req.tags, "at": self.d.clock.now().isoformat()},
            )
            return WriteResult(memory_id=None, status="active", session=True)

        # Durable scopes hit the per-tenant schema — ensure it exists (BR-14
        # fallback for tenants that predate the running consumers).
        await ensure_tenant_provisioned(self.d, ctx.tenant_id)

        policy = await self._policy(ctx.tenant_id)

        # (2) injection screening — fail closed if unavailable (BR-1). The
        # tenant injection profile (MEM-FR-051) tunes the screener sensitivity.
        score = await self.d.screener.score(ctx.tenant_id, req.content)
        threshold = self.s.injection_block_threshold
        if policy.injection_profile == "strict":
            threshold = min(threshold, 0.5)
        quarantined = score >= threshold

        # (3) PII policy scan — reject disallowed classes (BR-15 applies to
        # user_explicit too).
        if policy.pii_classes:
            found = await self.d.pii.scan(req.content, policy.pii_classes)
            if found:
                raise PiiRejected(f"content contains disallowed PII classes: {found}")

        now = self.d.clock.now()
        ttl = pol.resolve_ttl(req.scope, self.s, policy.ttl_overrides)
        confidence = (
            req.confidence
            if req.confidence is not None
            else pol.default_confidence(req.provenance["source_type"], self.s)
        )

        # Quarantined content is never embedded / retrievable — persist directly.
        if quarantined:
            return await self._persist_quarantined(ctx, req, ttl, now, score)

        # (4) embed → (4b) dedup/merge → (5) cap → persist. On embedding outage
        # (BR-2) the already-screened + PII-checked write is queued in mem:pend
        # (≤1h) rather than persisted unembedded (AC-11 write path).
        try:
            return await self._embed_and_persist(ctx, req, confidence, ttl, now, score)
        except EmbeddingUnavailable:
            await self._enqueue_pending(ctx, req, confidence, score, now)
            return WriteResult(memory_id=None, status="queued", degraded=True)

    async def _embed_and_persist(self, ctx, req, confidence, ttl, now, score) -> WriteResult:
        embedding = await self.d.embedder.embed(ctx.tenant_id, req.content)
        match = await self.d.store.find_similar(
            ctx.tenant_id, req.scope, req.scope_ref, embedding, self.s.dedup_threshold)
        if match is not None:
            return await self._merge(ctx, match[0], req, confidence, ttl, now)
        rec = MemoryRecord(
            memory_id=new_id(), tenant_id=ctx.tenant_id, scope=req.scope,
            scope_ref=req.scope_ref, content=req.content, embedding=embedding,
            provenance=[req.provenance], confidence=confidence,
            ttl_expires_at=now + ttl, revalidate_at=now + ttl * self.s.revalidate_fraction,
            status=STATUS_ACTIVE, tags=list(req.tags),
            classifier_score=round(score, 6), created_at=now, updated_at=now)
        evicted, envelopes = await self._enforce_cap(ctx, req.scope, req.scope_ref, now)
        audit = [self._audit(rec.memory_id, "write", ctx, req.content)]
        envelopes.append((TOPIC, make_envelope(
            event_type="memory.written", tenant_id=ctx.tenant_id, actor=ctx.actor,
            resource_urn=memory_urn(ctx.tenant_id, rec.memory_id),
            payload={"memory_id": rec.memory_id, "scope": rec.scope,
                     "source_type": req.provenance["source_type"], "merged": False},
            via_agent=ctx.via_agent, trace_id=ctx.trace_id)))
        await self.d.store.commit_write(rec, evicted=evicted, audit=audit, envelopes=envelopes)
        return WriteResult(memory_id=rec.memory_id, status="active")

    async def _persist_quarantined(self, ctx, req, ttl, now, score) -> WriteResult:
        rec = MemoryRecord(
            memory_id=new_id(), tenant_id=ctx.tenant_id, scope=req.scope,
            scope_ref=req.scope_ref, content=req.content, embedding=None,
            provenance=[req.provenance], confidence=(
                req.confidence if req.confidence is not None
                else pol.default_confidence(req.provenance["source_type"], self.s)),
            ttl_expires_at=now + ttl, revalidate_at=now + ttl * self.s.revalidate_fraction,
            status=STATUS_QUARANTINED, tags=list(req.tags),
            classifier_score=round(score, 6), created_at=now, updated_at=now)
        audit = [self._audit(rec.memory_id, "quarantine", ctx, req.content)]
        env = make_envelope(
            event_type="memory.quarantined", tenant_id=ctx.tenant_id, actor=ctx.actor,
            resource_urn=memory_urn(ctx.tenant_id, rec.memory_id),
            payload={"memory_id": rec.memory_id, "scope": rec.scope,
                     "source_type": req.provenance["source_type"], "merged": False,
                     "classifier_score": rec.classifier_score},
            via_agent=ctx.via_agent, trace_id=ctx.trace_id)
        await self.d.store.commit_write(rec, evicted=[], audit=audit, envelopes=[(TOPIC, env)])
        return WriteResult(memory_id=rec.memory_id, status="quarantined")

    async def _enqueue_pending(self, ctx, req, confidence, score, now) -> None:
        await self.d.pending.enqueue({
            "id": new_id(), "tenant_id": ctx.tenant_id, "scope": req.scope,
            "scope_ref": req.scope_ref, "content": req.content,
            "provenance": req.provenance, "confidence": confidence,
            "tags": list(req.tags), "score": round(score, 6),
            "actor": ctx.actor, "via_agent": ctx.via_agent, "trace_id": ctx.trace_id,
            "enqueued_at": now.isoformat()})

    async def drain_pending(self, tenant_id: str) -> dict:
        """Retry queued writes (BR-2). Persists any whose embeddings now succeed,
        fails those past the ≤1h window (never persisted unembedded)."""
        now = self.d.clock.now()
        window = timedelta(seconds=self.s.pending_window_seconds)
        policy = await self._policy(tenant_id)
        processed = failed = 0
        for e in await self.d.pending.list_all(tenant_id):
            enqueued = datetime.fromisoformat(e["enqueued_at"])
            if now - enqueued > window:
                await self.d.pending.remove(tenant_id, e["id"])
                await self.d.store.add_outbox(tenant_id, TOPIC, make_envelope(
                    event_type="memory.deleted", tenant_id=tenant_id, actor=e["actor"],
                    resource_urn=memory_urn(tenant_id, e["id"]),
                    payload={"memory_id": None, "reason": "embed_timeout"},
                    trace_id=e.get("trace_id")))
                failed += 1
                continue
            ctx = CallCtx(tenant_id=tenant_id, actor=e["actor"], via_agent=e.get("via_agent"),
                          trace_id=e.get("trace_id"))
            req = WriteRequest(scope=e["scope"], scope_ref=e["scope_ref"],
                               content=e["content"], provenance=e["provenance"],
                               confidence=e["confidence"], tags=e.get("tags", []))
            ttl = pol.resolve_ttl(req.scope, self.s, policy.ttl_overrides)
            try:
                await self._embed_and_persist(ctx, req, e["confidence"], ttl, now, e["score"])
                await self.d.pending.remove(tenant_id, e["id"])
                processed += 1
            except EmbeddingUnavailable:
                continue  # still down — leave queued for the next drain
        remaining = len(await self.d.pending.list_all(tenant_id))
        return {"processed": processed, "failed": failed, "remaining": remaining}

    async def _merge(self, ctx, existing: MemoryRecord, req, confidence, ttl, now) -> WriteResult:
        # BR-5: merge is idempotent, keeps the older memory_id, unions tags,
        # takes max confidence, refreshes TTL, appends provenance, replaces
        # content only if the new confidence is higher.
        existing.tags = sorted(set(existing.tags) | set(req.tags))
        if req.provenance not in existing.provenance:
            existing.provenance = [*existing.provenance, req.provenance]
        if confidence > existing.confidence:
            existing.confidence = confidence
            existing.content = req.content
        existing.ttl_expires_at = now + ttl
        existing.revalidate_at = now + ttl * self.s.revalidate_fraction
        if existing.memory_id not in existing.merged_from:
            existing.merged_from = [*existing.merged_from, existing.memory_id]
        existing.status = STATUS_ACTIVE
        existing.updated_at = now
        audit = [self._audit(existing.memory_id, "merge", ctx, req.content)]
        env = make_envelope(
            event_type="memory.written", tenant_id=ctx.tenant_id, actor=ctx.actor,
            resource_urn=memory_urn(ctx.tenant_id, existing.memory_id),
            payload={"memory_id": existing.memory_id, "scope": existing.scope,
                     "source_type": req.provenance["source_type"], "merged": True},
            via_agent=ctx.via_agent, trace_id=ctx.trace_id,
        )
        await self.d.store.commit_write(
            existing, evicted=[], audit=audit, envelopes=[(TOPIC, env)], is_update=True
        )
        return WriteResult(memory_id=existing.memory_id, status="active", merged=True)

    async def _enforce_cap(self, ctx, scope, scope_ref, now):
        cap = pol.scope_cap(scope, self.s)
        if cap is None:
            return [], []
        count = await self.d.store.count_active(ctx.tenant_id, scope, scope_ref)
        if count < cap:
            return [], []
        skip_after = now - timedelta(days=self.s.cap_eviction_skip_days)
        victim = await self.d.store.eviction_candidate(
            ctx.tenant_id, scope, scope_ref, now,
            pol.half_life_seconds(scope, self.s), skip_after,
        )
        if victim is None:
            return [], []
        env = make_envelope(
            event_type="memory.expired", tenant_id=ctx.tenant_id, actor=ctx.actor,
            resource_urn=memory_urn(ctx.tenant_id, victim.memory_id),
            payload={"memory_id": victim.memory_id, "reason": "cap"},
            trace_id=ctx.trace_id,
        )
        return [victim], [(TOPIC, env)]

    def _audit(self, memory_id, action, ctx: CallCtx, content: str) -> dict:
        return {
            "id": new_id(), "tenant_id": ctx.tenant_id, "memory_id": memory_id,
            "action": action, "actor": ctx.actor, "trace_id": ctx.trace_id,
            "created_at": self.d.clock.now(),
        }

    async def write_batch(self, ctx: CallCtx, reqs: list[WriteRequest]) -> list[dict]:
        if len(reqs) > self.s.batch_max:
            raise ValidationFailed(f"batch exceeds {self.s.batch_max}")
        results = []
        for r in reqs:
            try:
                res = await self.write(ctx, r)
                results.append({"status": res.status, "memory_id": res.memory_id,
                                "merged": res.merged})
            except (ValidationFailed, PiiRejected, ScopeDenied) as exc:
                results.append({"status": "rejected", "code": exc.code,
                                "message": exc.message})
        return results


# --------------------------------------------------------------------------- #
# Retrieval (MEM-FR-020..024)                                                  #
# --------------------------------------------------------------------------- #
class RetrievalService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps
        self.s = deps.settings

    async def retrieve(
        self, ctx: CallCtx, *, query_text: str | None, query_embedding: list[float] | None,
        scopes: list[tuple[str, str]], corpora: list[str], top_k: int,
        min_confidence: float | None, tags: list[str] | None,
        snapshot_ver: str | None, include_debug: bool,
    ) -> tuple[list[ScoredResult], bool]:
        top_k = min(top_k or 8, self.s.retrieve_top_k_max)
        # Ensure the tenant schema exists before querying it (BR-14 fallback):
        # a never-provisioned tenant retrieves an empty list, not a 500.
        await ensure_tenant_provisioned(self.d, ctx.tenant_id)
        now = self.d.clock.now()
        degraded = False
        embedding = query_embedding
        if embedding is None and query_text:
            try:
                embedding = await self.d.embedder.embed(ctx.tenant_id, query_text)
            except Exception:  # noqa: BLE001 — BR-2 degrade to recency+tag ranking
                degraded = True
                embedding = None

        results: list[ScoredResult] = []
        bump_ids: list[str] = []

        # Memories
        if scopes:
            rows = await self.d.store.search_memories(
                ctx.tenant_id, scopes, embedding, top_k,
                min_confidence=min_confidence, tags=tags,
            )
            for rec, sim in rows:
                score, dbg = blend(
                    similarity=sim, confidence=rec.confidence,
                    reference_time=rec.last_retrieved_at or rec.created_at,
                    now=now, scope=rec.scope, settings=self.s,
                    default_conf=rec.confidence,
                )
                results.append(ScoredResult(
                    kind="memory", content=rec.content, score=score, scope=rec.scope,
                    memory_id=rec.memory_id, provenance=rec.primary_provenance,
                    debug=dbg if include_debug else None,
                ))
                bump_ids.append(rec.memory_id)

        # RAG corpora
        if corpora and embedding is not None:
            active_ver = {}
            for ck in corpora:
                c = await self.d.store.get_corpus(ctx.tenant_id, ck)
                if c and c.status != "paused":
                    active_ver[ck] = c.active_embedding_ver
            if active_ver:
                chunks = await self.d.store.search_chunks(
                    ctx.tenant_id, list(active_ver), embedding, top_k,
                    active_ver=active_ver, snapshot_ver=snapshot_ver,
                )
                for chunk, sim in chunks:
                    score, dbg = blend(
                        similarity=sim, confidence=None,
                        reference_time=chunk.source_updated_at, now=now,
                        scope="workspace", settings=self.s,
                        default_conf=self.s.default_conf_for_chunk,
                    )
                    results.append(ScoredResult(
                        kind="chunk", content=chunk.content, score=score,
                        chunk_id=chunk.chunk_id, corpus=chunk.corpus_key,
                        source_urn=chunk.source_urn, snapshot_ver=chunk.snapshot_ver,
                        debug=dbg if include_debug else None,
                    ))

        results.sort(key=lambda r: r.score, reverse=True)
        results = results[: top_k * (1 + len(corpora))]
        # async confidence bump on retrieved memories (MEM-FR-013)
        if bump_ids:
            await self.d.store.bump_retrieval(
                ctx.tenant_id, bump_ids, now, self.s.conf_retrieval_bump, self.s.conf_cap
            )
        return results, degraded


# --------------------------------------------------------------------------- #
# Sessions (MEM-FR-002, AC-12)                                                 #
# --------------------------------------------------------------------------- #
class SessionService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps

    async def wipe(self, tenant_id: str, session_id: str) -> None:
        await self.d.session_store.wipe(tenant_id, session_id)


# --------------------------------------------------------------------------- #
# Corpora (MEM-FR-030..034)                                                    #
# --------------------------------------------------------------------------- #
class CorpusService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps
        self.s = deps.settings

    async def register(self, ctx: CallCtx, spec: dict) -> Corpus:
        corpus = Corpus(
            corpus_key=spec["corpus_key"], tenant_id=ctx.tenant_id,
            source=spec.get("source", {"kind": "cdc", "topics": []}),
            chunking=spec.get("chunking", {
                "strategy": "fixed", "max_tokens": self.s.chunk_max_tokens,
                "overlap": self.s.chunk_overlap}),
            active_embedding_ver=spec.get("embedding_model_ver", self.s.active_embedding_ver),
            refresh=spec.get("refresh", {"mode": "streaming"}),
            anonymization_profile=spec.get("anonymization_profile"),
            status="active", created_at=self.d.clock.now(), updated_at=self.d.clock.now(),
        )
        await self.d.store.upsert_corpus(corpus)
        return corpus

    async def patch(self, ctx: CallCtx, corpus_key: str, changes: dict) -> Corpus:
        c = await self.d.store.get_corpus(ctx.tenant_id, corpus_key)
        if c is None:
            raise NotFound("corpus not found")
        for k in ("source", "chunking", "refresh", "anonymization_profile", "status"):
            if k in changes:
                setattr(c, k, changes[k])
        c.updated_at = self.d.clock.now()
        await self.d.store.upsert_corpus(c)
        return c

    async def ingest_event(self, tenant_id: str, env: dict) -> int:
        """CDC ingestion (MEM-FR-031): map -> anonymize -> chunk -> embed ->
        upsert. Returns the number of chunks written. Idempotent by chunk key."""
        mapped = cm.map_event(env)
        if mapped is None:
            return 0
        corpus = await self.d.store.get_corpus(tenant_id, mapped.corpus_key)
        if corpus is None or corpus.status == "paused":
            return 0
        # tombstone: source deleted -> remove its chunks
        if mapped.tombstone:
            await self.d.store.delete_chunks_by_source(
                tenant_id, mapped.corpus_key, mapped.source_urn)
            return 0
        text = mapped.text
        if corpus.anonymization_profile is not None:
            text = await self.d.anonymizer.anonymize(text, corpus.anonymization_profile)
        chunks, _capped = chunk_text(
            text, max_tokens=corpus.chunking.get("max_tokens", self.s.chunk_max_tokens),
            overlap=corpus.chunking.get("overlap", self.s.chunk_overlap),
            max_bytes=self.s.chunk_content_max_bytes, cap=self.s.chunk_cap_per_source,
        )
        # Replace existing chunks for this source (update => replace, not dup).
        await self.d.store.delete_chunks_by_source(
            tenant_id, mapped.corpus_key, mapped.source_urn)
        now = self.d.clock.now()
        written = 0
        for seq, content in enumerate(chunks):
            emb = await self.d.embedder.embed(tenant_id, content)
            await self.d.store.upsert_chunk(RagChunk(
                chunk_id=new_id(), tenant_id=tenant_id, corpus_key=mapped.corpus_key,
                source_urn=mapped.source_urn, chunk_seq=seq, content=content,
                embedding=emb, embedding_model_ver=corpus.active_embedding_ver,
                snapshot_ver=now.date().isoformat(),
                source_updated_at=now, user_linkage=mapped.user_linkage, created_at=now,
            ))
            written += 1
        return written

    async def rebuild(self, ctx: CallCtx, corpus_key: str, new_ver: str) -> dict:
        """Re-embed on model-version bump (MEM-FR-033, AC-10): mark rebuilding,
        dual-write every chunk under ``new_ver``, switch active_embedding_ver
        atomically, then drop the old vectors. Retrieval never mixes versions
        because ``search_chunks`` filters by the corpus active_embedding_ver."""
        c = await self.d.store.get_corpus(ctx.tenant_id, corpus_key)
        if c is None:
            raise NotFound("corpus not found")
        if c.status == "rebuilding":
            raise Conflict("rebuild already in progress")
        old_ver = c.active_embedding_ver
        if new_ver == old_ver:
            raise Conflict("embedding version unchanged")
        c.status = "rebuilding"
        await self.d.store.upsert_corpus(c)
        # dual-write: re-embed existing (old-ver) chunks under the new version
        existing = await self.d.store.list_chunks(ctx.tenant_id, corpus_key, ver=old_ver)
        now = self.d.clock.now()
        for ch in existing:
            emb = await self.d.embedder.embed(ctx.tenant_id, ch.content)
            await self.d.store.upsert_chunk(RagChunk(
                chunk_id=new_id(), tenant_id=ctx.tenant_id, corpus_key=corpus_key,
                source_urn=ch.source_urn, chunk_seq=ch.chunk_seq, content=ch.content,
                embedding=emb, embedding_model_ver=new_ver, snapshot_ver=ch.snapshot_ver,
                source_updated_at=ch.source_updated_at, user_linkage=ch.user_linkage,
                created_at=now))
        # atomic switch, then drop old vectors
        c.active_embedding_ver = new_ver
        c.status = "active"
        c.updated_at = now
        await self.d.store.upsert_corpus(c)
        dropped = await self.d.store.switch_embedding_ver(ctx.tenant_id, corpus_key, new_ver)
        return {"corpus_key": corpus_key, "active_embedding_ver": new_ver,
                "chunks_reembedded": len(existing), "old_chunks_dropped": dropped}

    async def status(self, ctx: CallCtx, corpus_key: str) -> dict:
        c = await self.d.store.get_corpus(ctx.tenant_id, corpus_key)
        if c is None:
            raise NotFound("corpus not found")
        count = await self.d.store.count_chunks(ctx.tenant_id, corpus_key)
        return {"corpus_key": corpus_key, "status": c.status,
                "active_embedding_ver": c.active_embedding_ver, "chunk_count": count}

    async def add_document(self, ctx: CallCtx, source_urn: str, text: str) -> int:
        """docs corpus api_push (MEM-FR-031)."""
        env = {"event_type": "doc.pushed", "tenant_id": ctx.tenant_id,
               "resource_urn": source_urn, "payload": {}}
        # docs mapper is api_push; construct a MappedSource inline via ingest.
        corpus = await self.d.store.get_corpus(ctx.tenant_id, "docs")
        if corpus is None:
            raise NotFound("docs corpus not registered")
        chunks, _ = chunk_text(
            text, max_tokens=corpus.chunking.get("max_tokens", self.s.chunk_max_tokens),
            overlap=corpus.chunking.get("overlap", self.s.chunk_overlap),
            max_bytes=self.s.chunk_content_max_bytes, cap=self.s.chunk_cap_per_source)
        await self.d.store.delete_chunks_by_source(ctx.tenant_id, "docs", source_urn)
        now = self.d.clock.now()
        for seq, content in enumerate(chunks):
            emb = await self.d.embedder.embed(ctx.tenant_id, content)
            await self.d.store.upsert_chunk(RagChunk(
                chunk_id=new_id(), tenant_id=ctx.tenant_id, corpus_key="docs",
                source_urn=source_urn, chunk_seq=seq, content=content, embedding=emb,
                embedding_model_ver=corpus.active_embedding_ver,
                snapshot_ver=now.date().isoformat(), source_updated_at=now, created_at=now))
        _ = env
        return len(chunks)


# --------------------------------------------------------------------------- #
# Right-to-erasure (MEM-FR-040, AC-7)                                          #
# --------------------------------------------------------------------------- #
class ErasureService:
    """In-process orchestrator running the SAME idempotent activities Temporal
    drives in production (each step retried, verification gate before
    completion). No stubbed store calls — every activity hits a real store."""

    def __init__(self, deps: ServiceDeps):
        self.d = deps
        self.s = deps.settings

    async def start(self, ctx: CallCtx, subject_type: str, subject_id: str) -> ErasureRequest:
        await ensure_tenant_provisioned(self.d, ctx.tenant_id)
        req = ErasureRequest(
            request_id=new_id(), tenant_id=ctx.tenant_id, subject_type=subject_type,
            subject_id=subject_id, status="received",
            workflow_id=f"erasure-{ctx.tenant_id}-{subject_id}",
            created_at=self.d.clock.now(),
        )
        await self.d.store.add_erasure(req)
        # Run the workflow (in prod this is a Temporal workflow handle).
        asyncio.create_task(self._run(ctx, req))  # noqa: RUF006
        return req

    async def run_sync(self, ctx: CallCtx, req: ErasureRequest) -> ErasureRequest:
        return await self._run(ctx, req)

    async def _run(self, ctx: CallCtx, req: ErasureRequest) -> ErasureRequest:
        tid, sid = ctx.tenant_id, req.subject_id
        req.status = "running"
        await self.d.store.update_erasure(req)
        counts = {}
        # 1: user-scope memories (subject as scope_ref)
        counts["user_scope_memories"] = await self.d.store.delete_by_scope_ref(
            tid, SCOPE_USER, sid)
        # 2: provenance-linked memories in other scopes
        counts["provenance_linked_memories"] = await self.d.store.delete_by_provenance_user(
            tid, sid)
        # 3: user-attributable RAG chunks
        counts["rag_chunks"] = await self.d.store.delete_chunks_by_user(tid, sid)
        # 4: purge session memories referencing the subject
        counts["session_scope"] = await self.d.session_store.purge_subject(tid, sid)
        # 5: verification sweep
        req.status = "verifying"
        await self.d.store.update_erasure(req)
        probes = {
            "user_scope_memories": await self.d.store.count_active(tid, SCOPE_USER, sid),
            "provenance_linked_memories": await self.d.store.count_provenance_user(tid, sid),
            "rag_chunks": await self.d.store.count_chunks_by_user(tid, sid),
            "session_scope": await self.d.session_store.scan_subject(tid, sid),
        }
        all_green = all(v == 0 for v in probes.values())
        # 6: sign report + emit
        report = {
            "request_id": req.request_id, "subject_digest": _digest(sid),
            "counts_deleted": counts, "verification_queries": probes,
            "verified": all_green, "completed_at": self.d.clock.now().isoformat(),
        }
        req.report = report
        req.status = "completed" if all_green else "failed"
        req.completed_at = self.d.clock.now()
        env = make_envelope(
            event_type="erasure.completed", tenant_id=tid, actor=ctx.actor,
            resource_urn=erasure_urn(tid, req.request_id),
            payload={"subject_digest": _digest(sid), "counts": counts,
                     "report_ref": req.request_id, "verified": all_green},
            trace_id=ctx.trace_id,
        )
        await self.d.store.update_erasure(req)
        await self.d.store.add_outbox(tid, TOPIC, env)
        return req


def _digest(value: str) -> str:
    from app.utils import sha256_hex
    return sha256_hex(value)[:16]


# --------------------------------------------------------------------------- #
# Retention / re-validation jobs (MEM-FR-041/042, AC-8)                        #
# --------------------------------------------------------------------------- #
class RetentionService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps
        self.s = deps.settings

    async def run_expiry(self, tenant_id: str) -> dict:
        await ensure_tenant_provisioned(self.d, tenant_id)
        now = self.d.clock.now()
        expired = await self.d.store.expire_past_ttl(tenant_id, now)
        grace = now - timedelta(days=self.s.expire_grace_days)
        hard = await self.d.store.hard_delete_expired(tenant_id, grace)
        qcut = now - timedelta(days=self.s.quarantine_purge_days)
        purged = await self.d.store.purge_quarantined(tenant_id, qcut)
        return {"expired": expired, "hard_deleted": hard, "quarantined_purged": purged}

    async def run_revalidation(self, tenant_id: str) -> dict:
        await ensure_tenant_provisioned(self.d, tenant_id)
        now = self.d.clock.now()
        decayed, expired = await self.d.store.revalidate(
            tenant_id, now, self.s.revalidate_decay, self.s.revalidate_expire_below,
            self.s.revalidate_fraction,
        )
        return {"decayed": decayed, "expired": expired}


# --------------------------------------------------------------------------- #
# Tenant policy (MEM-FR-051, AC-13)                                            #
# --------------------------------------------------------------------------- #
class PolicyService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps
        self.s = deps.settings

    async def get(self, tenant_id: str) -> TenantPolicy:
        return await self.d.store.get_policy(tenant_id) or TenantPolicy(tenant_id=tenant_id)

    async def put(self, tenant_id: str, body: dict) -> TenantPolicy:
        overrides = body.get("ttl_overrides", {})
        for scope, val in overrides.items():
            if scope not in DURABLE_SCOPES:
                raise ValidationFailed(f"unknown scope {scope}")
            pol.validate_ttl_override(scope, val, self.s)
        policy = TenantPolicy(
            tenant_id=tenant_id, ttl_overrides=overrides,
            pii_classes=body.get("pii_classes", []),
            injection_profile=body.get("injection_profile", "standard"),
            corpus_flags=body.get("corpus_flags", {}),
            updated_at=self.d.clock.now(),
        )
        await self.d.store.put_policy(policy)
        return policy


# --------------------------------------------------------------------------- #
# Browse / admin (MEM-FR-050/052)                                             #
# --------------------------------------------------------------------------- #
class AdminService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps
        self.s = deps.settings

    async def list_memories(self, ctx, *, scope, status, tags, scope_ref, limit, cursor):
        await ensure_tenant_provisioned(self.d, ctx.tenant_id)
        return await self.d.store.list_memories(
            ctx.tenant_id, scope=scope, status=status, tags=tags,
            scope_ref=scope_ref, limit=limit, cursor=cursor,
        )

    async def get(self, ctx, memory_id) -> MemoryRecord:
        await ensure_tenant_provisioned(self.d, ctx.tenant_id)
        rec = await self.d.store.get_memory(ctx.tenant_id, memory_id)
        if rec is None or rec.status == "deleted":
            raise NotFound("memory not found")
        return rec

    async def edit(self, ctx, memory_id, content) -> MemoryRecord:
        rec = await self.get(ctx, memory_id)
        now = self.d.clock.now()
        rec.content = content
        rec.embedding = await self.d.embedder.embed(ctx.tenant_id, content)
        rec.provenance = [Provenance(source_type=SRC_ADMIN, user_id=ctx.subject).to_dict()]
        rec.updated_at = now
        env = make_envelope(
            event_type="memory.edited", tenant_id=ctx.tenant_id, actor=ctx.actor,
            resource_urn=memory_urn(ctx.tenant_id, memory_id),
            payload={"memory_id": memory_id}, trace_id=ctx.trace_id)
        await self.d.store.commit_write(
            rec, evicted=[], audit=[{"id": new_id(), "tenant_id": ctx.tenant_id,
            "memory_id": memory_id, "action": "edit", "actor": ctx.actor,
            "trace_id": ctx.trace_id, "created_at": now}],
            envelopes=[(TOPIC, env)], is_update=True)
        return rec

    async def delete(self, ctx, memory_id) -> None:
        rec = await self.get(ctx, memory_id)
        await self.d.store.hard_delete_memory(ctx.tenant_id, memory_id)
        env = make_envelope(
            event_type="memory.deleted", tenant_id=ctx.tenant_id, actor=ctx.actor,
            resource_urn=memory_urn(ctx.tenant_id, memory_id),
            payload={"memory_id": memory_id, "reason": "admin_delete"},
            trace_id=ctx.trace_id)
        await self.d.store.add_outbox(ctx.tenant_id, TOPIC, env)
        await self.d.store.add_audit(ctx.tenant_id, {
            "id": new_id(), "tenant_id": ctx.tenant_id, "memory_id": memory_id,
            "action": "delete", "actor": ctx.actor, "trace_id": ctx.trace_id,
            "created_at": self.d.clock.now()})
        _ = rec

    async def unquarantine(self, ctx, memory_id, reason: str) -> MemoryRecord:
        await ensure_tenant_provisioned(self.d, ctx.tenant_id)
        rec = await self.d.store.get_memory(ctx.tenant_id, memory_id)
        if rec is None or rec.status != STATUS_QUARANTINED:
            raise NotFound("quarantined memory not found")
        now = self.d.clock.now()
        rec.status = STATUS_ACTIVE
        if rec.embedding is None:
            rec.embedding = await self.d.embedder.embed(ctx.tenant_id, rec.content)
        rec.updated_at = now
        await self.d.store.commit_write(
            rec, evicted=[], audit=[{"id": new_id(), "tenant_id": ctx.tenant_id,
            "memory_id": memory_id, "action": "edit", "actor": ctx.actor,
            "reason": reason, "trace_id": ctx.trace_id, "created_at": now}],
            envelopes=[], is_update=True)
        return rec

    async def stats(self, ctx) -> dict:
        await ensure_tenant_provisioned(self.d, ctx.tenant_id)
        return await self.d.store.stats(ctx.tenant_id)


# --------------------------------------------------------------------------- #
# Provisioning + standard corpora (MEM-FR consumers §6, BR-14)                 #
# --------------------------------------------------------------------------- #
STANDARD_CORPORA = [
    {"corpus_key": "schemas", "source": {"kind": "cdc", "topics": ["dataset.events.v1"]},
     "refresh": {"mode": "streaming"}, "anonymization_profile": None},
    {"corpus_key": "dashboards", "source": {"kind": "cdc", "topics": ["chart.events.v1"]},
     "refresh": {"mode": "streaming"}, "anonymization_profile": None},
    {"corpus_key": "resolved_cases", "source": {"kind": "cdc", "topics": ["case.events.v1"]},
     "refresh": {"mode": "streaming"},
     "anonymization_profile": {"drop_classes": ["PERSON", "EMAIL", "PHONE", "SSN"]}},
    {"corpus_key": "docs", "source": {"kind": "api_push", "topics": []},
     "refresh": {"mode": "on_push"}, "anonymization_profile": {"scan_only": True}},
]


class ProvisioningService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps
        self.s = deps.settings

    async def provision(self, tenant_id: str) -> None:
        await self.d.store.provision_tenant(tenant_id)
        # default policy + standard corpora rows
        if await self.d.store.get_policy(tenant_id) is None:
            await self.d.store.put_policy(TenantPolicy(
                tenant_id=tenant_id, updated_at=self.d.clock.now()))
        for spec in STANDARD_CORPORA:
            if await self.d.store.get_corpus(tenant_id, spec["corpus_key"]) is None:
                await self.d.store.upsert_corpus(Corpus(
                    corpus_key=spec["corpus_key"], tenant_id=tenant_id,
                    source=spec["source"],
                    chunking={"strategy": "fixed",
                              "max_tokens": self.s.chunk_max_tokens,
                              "overlap": self.s.chunk_overlap},
                    active_embedding_ver=self.s.active_embedding_ver,
                    refresh=spec["refresh"],
                    anonymization_profile=spec["anonymization_profile"],
                    status="active", created_at=self.d.clock.now(),
                    updated_at=self.d.clock.now()))

    async def ready(self, tenant_id: str) -> bool:
        return await self.d.store.tenant_ready(tenant_id)


_ = (SCOPE_WORKSPACE, corpus_urn)  # referenced by API/tests
