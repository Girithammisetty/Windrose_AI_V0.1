"""In-memory store — unit-tier double implementing the MemoryStore port.

NOT wired into the real runtime container (SqlMemoryStore is). Cosine similarity
and ranking are computed in Python; behaviour mirrors the SQL store's semantics
so domain tests exercise real logic without a database.
"""

from __future__ import annotations

import copy
from datetime import datetime

from app.domain.entities import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_QUARANTINED,
    Corpus,
    ErasureRequest,
    MemoryRecord,
    RagChunk,
    TenantPolicy,
)
from app.domain.ports import Page
from app.utils import cosine, decode_cursor, encode_cursor, recency_decay


class MemoryStore:
    def __init__(self):
        self.provisioned: set[str] = set()
        self.memories: dict[tuple[str, str], MemoryRecord] = {}
        self.chunks: dict[str, RagChunk] = {}
        self.corpora: dict[tuple[str, str], Corpus] = {}
        self.policies: dict[str, TenantPolicy] = {}
        self.erasures: dict[tuple[str, str], ErasureRequest] = {}
        self.audit: list[dict] = []
        self.outbox: list[tuple[str, dict]] = []
        self.idempotency: dict[tuple[str, str], dict] = {}

    # ---- provisioning ----
    async def provision_tenant(self, tenant_id: str) -> None:
        self.provisioned.add(tenant_id)

    async def drop_tenant(self, tenant_id: str) -> None:
        self.provisioned.discard(tenant_id)
        self.memories = {k: v for k, v in self.memories.items() if k[0] != tenant_id}
        self.chunks = {k: v for k, v in self.chunks.items() if v.tenant_id != tenant_id}

    async def tenant_ready(self, tenant_id: str) -> bool:
        return tenant_id in self.provisioned

    async def ping(self) -> bool:
        return True

    # ---- memories ----
    def _active(self, tenant_id, scope=None, scope_ref=None):
        for (t, _mid), rec in self.memories.items():
            if t != tenant_id or rec.status != STATUS_ACTIVE:
                continue
            if scope is not None and rec.scope != scope:
                continue
            if scope_ref is not None and rec.scope_ref != scope_ref:
                continue
            yield rec

    async def commit_write(self, rec, *, evicted, audit, envelopes, is_update=False):
        self.memories[(rec.tenant_id, rec.memory_id)] = copy.deepcopy(rec)
        for victim in evicted:
            v = self.memories.get((victim.tenant_id, victim.memory_id))
            if v is not None:
                v.status = STATUS_EXPIRED
                v.updated_at = rec.updated_at
        self.audit.extend(audit)
        self.outbox.extend(envelopes)

    async def hard_delete_memory(self, tenant_id, memory_id) -> None:
        self.memories.pop((tenant_id, memory_id), None)

    async def get_memory(self, tenant_id, memory_id):
        rec = self.memories.get((tenant_id, memory_id))
        return copy.deepcopy(rec) if rec else None

    async def find_similar(self, tenant_id, scope, scope_ref, embedding, threshold):
        best = None
        best_sim = threshold
        for rec in self._active(tenant_id, scope, scope_ref):
            if rec.embedding is None:
                continue
            sim = cosine(embedding, rec.embedding)
            if sim >= best_sim:
                best_sim = sim
                best = rec
        return (copy.deepcopy(best), best_sim) if best else None

    async def search_memories(self, tenant_id, scopes, embedding, top_k, *,
                              min_confidence, tags):
        wanted = set(scopes)
        out = []
        for rec in self._active(tenant_id):
            if (rec.scope, rec.scope_ref) not in wanted:
                continue
            if min_confidence is not None and rec.confidence < min_confidence:
                continue
            if tags and not set(tags).issubset(set(rec.tags)):
                continue
            sim = cosine(embedding, rec.embedding) if (embedding and rec.embedding) else 0.0
            out.append((copy.deepcopy(rec), sim))
        if embedding:
            out.sort(key=lambda x: x[1], reverse=True)
        else:
            out.sort(key=lambda x: x[0].created_at or datetime.min, reverse=True)
        return out[:top_k]

    async def count_active(self, tenant_id, scope, scope_ref) -> int:
        return sum(1 for _ in self._active(tenant_id, scope, scope_ref))

    async def eviction_candidate(self, tenant_id, scope, scope_ref, now,
                                 half_life_seconds, skip_after):
        cand = None
        best = None
        for rec in self._active(tenant_id, scope, scope_ref):
            if rec.last_retrieved_at and rec.last_retrieved_at >= skip_after:
                continue
            age = (now - (rec.created_at or now)).total_seconds()
            score = rec.confidence * recency_decay(age, half_life_seconds)
            if best is None or score < best:
                best = score
                cand = rec
        return copy.deepcopy(cand) if cand else None

    async def bump_retrieval(self, tenant_id, memory_ids, now, inc, cap) -> None:
        for mid in memory_ids:
            rec = self.memories.get((tenant_id, mid))
            if rec:
                rec.retrieval_count += 1
                rec.last_retrieved_at = now
                rec.confidence = min(cap, rec.confidence + inc)

    async def list_memories(self, tenant_id, *, scope, status, tags, scope_ref,
                            limit, cursor):
        rows = []
        for (t, _mid), rec in self.memories.items():
            if t != tenant_id:
                continue
            if scope and rec.scope != scope:
                continue
            if status and rec.status != status:
                continue
            if scope_ref and rec.scope_ref != scope_ref:
                continue
            if tags and not set(tags).issubset(set(rec.tags)):
                continue
            rows.append(rec)
        rows.sort(key=lambda r: (r.created_at or datetime.min, r.memory_id), reverse=True)
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        window = rows[offset : offset + limit + 1]
        has_more = len(window) > limit
        return Page(
            items=[copy.deepcopy(r) for r in window[:limit]],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    # ---- erasure ----
    async def delete_by_scope_ref(self, tenant_id, scope, scope_ref) -> int:
        keys = [k for k, r in self.memories.items()
                if k[0] == tenant_id and r.scope == scope and r.scope_ref == scope_ref]
        for k in keys:
            self.memories.pop(k, None)
        return len(keys)

    async def delete_by_provenance_user(self, tenant_id, user_id) -> int:
        keys = [k for k, r in self.memories.items()
                if k[0] == tenant_id and r.scope != "user"
                and any(p.get("user_id") == user_id for p in r.provenance)]
        for k in keys:
            self.memories.pop(k, None)
        return len(keys)

    async def count_provenance_user(self, tenant_id, user_id) -> int:
        # Mirror delete_by_provenance_user exactly (scope != 'user'); user-scope
        # rows of the subject are removed by delete_by_scope_ref (erasure step 1).
        return sum(1 for k, r in self.memories.items()
                   if k[0] == tenant_id and r.scope != "user"
                   and any(p.get("user_id") == user_id for p in r.provenance))

    async def quarantine_by_run(self, tenant_id, run_id, now) -> int:
        n = 0
        for rec in self._active(tenant_id):
            if any(p.get("run_id") == run_id for p in rec.provenance):
                rec.status = STATUS_QUARANTINED
                rec.updated_at = now
                n += 1
        return n

    # ---- retention ----
    async def expire_past_ttl(self, tenant_id, now) -> int:
        n = 0
        for rec in self._active(tenant_id):
            if rec.ttl_expires_at <= now:
                rec.status = STATUS_EXPIRED
                rec.updated_at = now
                n += 1
        return n

    async def hard_delete_expired(self, tenant_id, cutoff) -> int:
        keys = [k for k, r in self.memories.items()
                if k[0] == tenant_id and r.status == STATUS_EXPIRED
                and (r.updated_at or cutoff) <= cutoff]
        for k in keys:
            self.memories.pop(k, None)
        return len(keys)

    async def revalidate(self, tenant_id, now, decay, expire_below,
                         revalidate_fraction) -> tuple[int, int]:
        decayed = expired = 0
        for rec in list(self._active(tenant_id)):
            if rec.revalidate_at > now:
                continue
            if rec.retrieval_count == 0:
                rec.confidence = max(0.0, rec.confidence - decay)
                decayed += 1
                if rec.confidence < expire_below:
                    rec.status = STATUS_EXPIRED
                    rec.updated_at = now
                    expired += 1
                else:
                    ttl_span = (rec.ttl_expires_at - now)
                    rec.revalidate_at = now + ttl_span * revalidate_fraction
            else:
                ttl_span = (rec.ttl_expires_at - now)
                rec.revalidate_at = now + ttl_span * revalidate_fraction
                rec.retrieval_count = 0
        return decayed, expired

    async def purge_quarantined(self, tenant_id, cutoff) -> int:
        keys = [k for k, r in self.memories.items()
                if k[0] == tenant_id and r.status == STATUS_QUARANTINED
                and (r.updated_at or cutoff) <= cutoff]
        for k in keys:
            self.memories.pop(k, None)
        return len(keys)

    # ---- chunks ----
    def _chunk_key(self, c: RagChunk) -> str:
        return f"{c.tenant_id}|{c.corpus_key}|{c.source_urn}|{c.chunk_seq}|{c.embedding_model_ver}"

    async def upsert_chunk(self, chunk) -> None:
        self.chunks[self._chunk_key(chunk)] = copy.deepcopy(chunk)

    async def delete_chunks_by_source(self, tenant_id, corpus_key, source_urn) -> int:
        keys = [k for k, c in self.chunks.items()
                if c.tenant_id == tenant_id and c.corpus_key == corpus_key
                and c.source_urn == source_urn]
        for k in keys:
            self.chunks.pop(k, None)
        return len(keys)

    async def delete_chunks_by_user(self, tenant_id, user_id) -> int:
        keys = [k for k, c in self.chunks.items()
                if c.tenant_id == tenant_id and c.user_linkage == user_id]
        for k in keys:
            self.chunks.pop(k, None)
        return len(keys)

    async def count_chunks_by_user(self, tenant_id, user_id) -> int:
        return sum(1 for c in self.chunks.values()
                   if c.tenant_id == tenant_id and c.user_linkage == user_id)

    async def list_chunks(self, tenant_id, corpus_key, *, ver=None):
        return [copy.deepcopy(c) for c in self.chunks.values()
                if c.tenant_id == tenant_id and c.corpus_key == corpus_key
                and (ver is None or c.embedding_model_ver == ver)]

    async def switch_embedding_ver(self, tenant_id, corpus_key, new_ver) -> int:
        keys = [k for k, c in self.chunks.items()
                if c.tenant_id == tenant_id and c.corpus_key == corpus_key
                and c.embedding_model_ver != new_ver]
        for k in keys:
            self.chunks.pop(k, None)
        return len(keys)

    async def search_chunks(self, tenant_id, corpora, embedding, top_k, *,
                            active_ver, snapshot_ver):
        out = []
        for c in self.chunks.values():
            if c.tenant_id != tenant_id or c.corpus_key not in corpora:
                continue
            if c.embedding_model_ver != active_ver.get(c.corpus_key):
                continue
            if snapshot_ver is not None and (c.snapshot_ver or "") > snapshot_ver:
                continue
            sim = cosine(embedding, c.embedding) if c.embedding else 0.0
            out.append((copy.deepcopy(c), sim))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:top_k]

    async def count_chunks(self, tenant_id, corpus_key) -> int:
        return sum(1 for c in self.chunks.values()
                   if c.tenant_id == tenant_id and c.corpus_key == corpus_key)

    # ---- control-plane ----
    async def get_corpus(self, tenant_id, corpus_key):
        c = self.corpora.get((tenant_id, corpus_key))
        return copy.deepcopy(c) if c else None

    async def list_corpora(self, tenant_id):
        return [copy.deepcopy(c) for (t, _k), c in self.corpora.items() if t == tenant_id]

    async def upsert_corpus(self, corpus) -> None:
        self.corpora[(corpus.tenant_id, corpus.corpus_key)] = copy.deepcopy(corpus)

    async def get_policy(self, tenant_id):
        p = self.policies.get(tenant_id)
        return copy.deepcopy(p) if p else None

    async def put_policy(self, policy) -> None:
        self.policies[policy.tenant_id] = copy.deepcopy(policy)

    async def add_erasure(self, req) -> None:
        self.erasures[(req.tenant_id, req.request_id)] = copy.deepcopy(req)

    async def update_erasure(self, req) -> None:
        self.erasures[(req.tenant_id, req.request_id)] = copy.deepcopy(req)

    async def get_erasure(self, tenant_id, request_id):
        r = self.erasures.get((tenant_id, request_id))
        return copy.deepcopy(r) if r else None

    async def add_audit(self, tenant_id, entry) -> None:
        self.audit.append(entry)

    async def list_audit(self, tenant_id, memory_id):
        return [a for a in self.audit
                if a.get("tenant_id") == tenant_id and a.get("memory_id") == memory_id]

    async def add_outbox(self, tenant_id, topic, envelope) -> None:
        self.outbox.append((topic, envelope))

    async def stats(self, tenant_id) -> dict:
        by_scope: dict[str, int] = {}
        quarantined = 0
        for (t, _mid), rec in self.memories.items():
            if t != tenant_id:
                continue
            if rec.status == STATUS_ACTIVE:
                by_scope[rec.scope] = by_scope.get(rec.scope, 0) + 1
            if rec.status == STATUS_QUARANTINED:
                quarantined += 1
        return {"active_by_scope": by_scope, "quarantined": quarantined,
                "chunks": sum(1 for c in self.chunks.values() if c.tenant_id == tenant_id)}

    async def idempotency_get(self, tenant_id, key):
        return self.idempotency.get((tenant_id, key))

    async def idempotency_put(self, tenant_id, key, request_hash, status, body) -> None:
        self.idempotency[(tenant_id, key)] = {
            "request_hash": request_hash, "status_code": status, "body": body}
