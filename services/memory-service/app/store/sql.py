"""SQL store (pgvector, schema-per-tenant + RLS) — the runtime MemoryStore.

Tenant memory/chunk tables live in ``mem_t_<tenant>``; every per-request session
pins ``search_path`` to that schema (+ public for control tables) AND sets
``app.tenant_id`` so RLS applies to the non-privileged ``memory_app`` role. The
retrieve/write SQL additionally carries an explicit ``tenant_id`` predicate — two
independent isolation layers (MEM-FR-021). pgvector ``<=>`` gives cosine ANN.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from app.store.schema import tenant_schema
from app.utils import decode_cursor, encode_cursor, new_id, recency_decay, utcnow

_MEM_COLS = ("memory_id, tenant_id, scope, scope_ref, content, embedding, provenance, "
             "confidence, ttl_expires_at, revalidate_at, tags, status, retrieval_count, "
             "last_retrieved_at, classifier_score, merged_from, created_at, updated_at")


def make_engine(database_url: str):
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )


def _vec(embedding: list[float] | None) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


def _parse_vec(literal) -> list[float] | None:
    if not literal:
        return None
    if isinstance(literal, list):
        return [float(x) for x in literal]
    return [float(x) for x in str(literal).strip("[]").split(",") if x]


def _jsonify(v):
    return v if isinstance(v, (list, dict)) else (json.loads(v) if v else v)


def _row_to_memory(r) -> MemoryRecord:
    m = r._mapping
    return MemoryRecord(
        memory_id=str(m["memory_id"]), tenant_id=str(m["tenant_id"]), scope=m["scope"],
        scope_ref=m["scope_ref"], content=m["content"], embedding=_parse_vec(m["embedding"]),
        provenance=_jsonify(m["provenance"]) or [], confidence=float(m["confidence"]),
        ttl_expires_at=m["ttl_expires_at"], revalidate_at=m["revalidate_at"],
        tags=list(m["tags"] or []), status=m["status"],
        retrieval_count=int(m["retrieval_count"]), last_retrieved_at=m["last_retrieved_at"],
        classifier_score=(float(m["classifier_score"])
                          if m["classifier_score"] is not None else None),
        merged_from=[str(x) for x in (m["merged_from"] or [])],
        created_at=m["created_at"], updated_at=m["updated_at"],
    )


def _row_to_chunk(r) -> RagChunk:
    m = r._mapping
    return RagChunk(
        chunk_id=str(m["chunk_id"]), tenant_id=str(m["tenant_id"]),
        corpus_key=m["corpus_key"], source_urn=m["source_urn"], chunk_seq=int(m["chunk_seq"]),
        content=m["content"], embedding=_parse_vec(m["embedding"]),
        embedding_model_ver=m["embedding_model_ver"], snapshot_ver=m["snapshot_ver"],
        source_updated_at=m["source_updated_at"], user_linkage=m["user_linkage"],
        created_at=m["created_at"],
    )


class SqlMemoryStore:
    def __init__(self, session_factory: async_sessionmaker,
                 admin_session_factory: async_sessionmaker):
        self._sf = session_factory
        self._admin_sf = admin_session_factory

    @asynccontextmanager
    async def _session(self, tenant_id: str):
        """Tenant-bound session: search_path -> tenant schema + public; RLS GUC."""
        s = self._sf()
        try:
            sch = tenant_schema(tenant_id)
            await s.execute(text(f'SET LOCAL search_path TO "{sch}", public'))
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                            {"t": tenant_id})
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
        finally:
            await s.close()

    # ---- provisioning (privileged) ----
    async def provision_tenant(self, tenant_id: str) -> None:
        s = self._admin_sf()
        try:
            # Serialize concurrent provisioning of the same tenant (the lazy
            # first-use path can race the tenant.provisioned consumer): the
            # IF NOT EXISTS DDL inside mem_provision_tenant is idempotent but
            # concurrent CREATE SCHEMA can still collide on pg_namespace. The
            # xact-scoped advisory lock is released at commit/rollback.
            await s.execute(text(
                "SELECT pg_advisory_xact_lock(hashtextextended("
                "'mem_provision:' || CAST(:t AS text), 0))"), {"t": tenant_id})
            await s.execute(text("SELECT mem_provision_tenant(:t)"), {"t": tenant_id})
            await s.commit()
        except Exception:
            await s.rollback()
            raise
        finally:
            await s.close()

    async def drop_tenant(self, tenant_id: str) -> None:
        s = self._admin_sf()
        try:
            await s.execute(text("SELECT mem_drop_tenant(:t)"), {"t": tenant_id})
            await s.commit()
        finally:
            await s.close()

    async def tenant_ready(self, tenant_id: str) -> bool:
        s = self._sf()
        try:
            row = (await s.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :n"),
                {"n": tenant_schema(tenant_id)})).first()
            return row is not None
        finally:
            await s.close()

    async def ping(self) -> bool:
        s = self._sf()
        try:
            await s.execute(text("SELECT 1"))
            return True
        finally:
            await s.close()

    # ---- memories ----
    async def _upsert_memory(self, s, rec: MemoryRecord) -> None:
        await s.execute(text(
            "INSERT INTO memories (memory_id, tenant_id, scope, scope_ref, content, "
            "embedding, provenance, confidence, ttl_expires_at, revalidate_at, tags, "
            "status, retrieval_count, last_retrieved_at, classifier_score, merged_from, "
            "created_at, updated_at) VALUES (:memory_id, :tenant_id, :scope, :scope_ref, "
            ":content, CAST(:embedding AS vector), CAST(:provenance AS jsonb), :confidence, "
            ":ttl, :reval, :tags, :status, :rc, :lra, :cs, :mf, :created, :updated) "
            "ON CONFLICT (memory_id) DO UPDATE SET content=excluded.content, "
            "embedding=excluded.embedding, provenance=excluded.provenance, "
            "confidence=excluded.confidence, ttl_expires_at=excluded.ttl_expires_at, "
            "revalidate_at=excluded.revalidate_at, tags=excluded.tags, status=excluded.status, "
            "retrieval_count=excluded.retrieval_count, "
            "last_retrieved_at=excluded.last_retrieved_at, "
            "classifier_score=excluded.classifier_score, merged_from=excluded.merged_from, "
            "updated_at=excluded.updated_at"),
            {"memory_id": rec.memory_id, "tenant_id": rec.tenant_id, "scope": rec.scope,
             "scope_ref": rec.scope_ref, "content": rec.content, "embedding": _vec(rec.embedding),
             "provenance": json.dumps(rec.provenance), "confidence": rec.confidence,
             "ttl": rec.ttl_expires_at, "reval": rec.revalidate_at, "tags": rec.tags,
             "status": rec.status, "rc": rec.retrieval_count, "lra": rec.last_retrieved_at,
             "cs": rec.classifier_score, "mf": rec.merged_from,
             "created": rec.created_at or utcnow(), "updated": rec.updated_at or utcnow()})

    async def commit_write(self, rec, *, evicted, audit, envelopes, is_update=False) -> None:
        async with self._session(rec.tenant_id) as s:
            await self._upsert_memory(s, rec)
            for v in evicted:
                await s.execute(text(
                    "UPDATE memories SET status='expired', updated_at=:now "
                    "WHERE memory_id=:id AND tenant_id=:t"),
                    {"now": rec.updated_at or utcnow(), "id": v.memory_id, "t": rec.tenant_id})
            for a in audit:
                await self._insert_audit(s, a)
            for topic, env in envelopes:
                await self._insert_outbox(s, rec.tenant_id, topic, env)

    async def hard_delete_memory(self, tenant_id, memory_id) -> None:
        async with self._session(tenant_id) as s:
            await s.execute(text("DELETE FROM memories WHERE memory_id=:id AND tenant_id=:t"),
                            {"id": memory_id, "t": tenant_id})

    async def get_memory(self, tenant_id, memory_id):
        async with self._session(tenant_id) as s:
            r = (await s.execute(text(
                f"SELECT {_MEM_COLS} FROM memories WHERE memory_id=:id AND tenant_id=:t"),
                {"id": memory_id, "t": tenant_id})).first()
            return _row_to_memory(r) if r else None

    async def find_similar(self, tenant_id, scope, scope_ref, embedding, threshold):
        async with self._session(tenant_id) as s:
            r = (await s.execute(text(
                f"SELECT {_MEM_COLS}, 1 - (embedding <=> CAST(:emb AS vector)) AS sim "
                "FROM memories WHERE status='active' AND tenant_id=:t AND scope=:sc "
                "AND scope_ref=:ref AND embedding IS NOT NULL "
                "ORDER BY embedding <=> CAST(:emb AS vector) ASC LIMIT 1"),
                {"emb": _vec(embedding), "t": tenant_id, "sc": scope, "ref": scope_ref})).first()
            if r is None:
                return None
            sim = float(r._mapping["sim"])
            return (_row_to_memory(r), sim) if sim >= threshold else None

    async def search_memories(self, tenant_id, scopes, embedding, top_k, *,
                              min_confidence, tags):
        if not scopes:
            return []
        conds, params = [], {"t": tenant_id, "k": top_k}
        for i, (sc, ref) in enumerate(scopes):
            conds.append(f"(scope=:sc{i} AND scope_ref=:ref{i})")
            params[f"sc{i}"], params[f"ref{i}"] = sc, ref
        where = " OR ".join(conds)
        extra = ""
        if min_confidence is not None:
            extra += " AND confidence >= :minc"
            params["minc"] = min_confidence
        if tags:
            extra += " AND tags @> :tags"
            params["tags"] = tags
        async with self._session(tenant_id) as s:
            if embedding is not None:
                params["emb"] = _vec(embedding)
                sql = (f"SELECT {_MEM_COLS}, 1 - (embedding <=> CAST(:emb AS vector)) AS sim "
                       f"FROM memories WHERE status='active' AND tenant_id=:t AND ({where}) "
                       f"AND embedding IS NOT NULL{extra} "
                       "ORDER BY embedding <=> CAST(:emb AS vector) ASC LIMIT :k")
            else:
                sql = (f"SELECT {_MEM_COLS}, 0.0 AS sim FROM memories "
                       f"WHERE status='active' AND tenant_id=:t AND ({where}){extra} "
                       "ORDER BY created_at DESC LIMIT :k")
            rows = (await s.execute(text(sql), params)).fetchall()
            return [(_row_to_memory(r), float(r._mapping["sim"])) for r in rows]

    async def count_active(self, tenant_id, scope, scope_ref) -> int:
        async with self._session(tenant_id) as s:
            return int((await s.execute(text(
                "SELECT count(*) FROM memories WHERE status='active' AND tenant_id=:t "
                "AND scope=:sc AND scope_ref=:ref"),
                {"t": tenant_id, "sc": scope, "ref": scope_ref})).scalar() or 0)

    async def eviction_candidate(self, tenant_id, scope, scope_ref, now,
                                 half_life_seconds, skip_after):
        async with self._session(tenant_id) as s:
            r = (await s.execute(text(
                f"SELECT {_MEM_COLS} FROM memories WHERE status='active' AND tenant_id=:t "
                "AND scope=:sc AND scope_ref=:ref AND (last_retrieved_at IS NULL "
                "OR last_retrieved_at < :skip) ORDER BY confidence * "
                "exp(- extract(epoch FROM (:now - created_at)) / :hl) ASC LIMIT 1"),
                {"t": tenant_id, "sc": scope, "ref": scope_ref, "skip": skip_after,
                 "now": now, "hl": half_life_seconds})).first()
            return _row_to_memory(r) if r else None

    async def bump_retrieval(self, tenant_id, memory_ids, now, inc, cap) -> None:
        if not memory_ids:
            return
        async with self._session(tenant_id) as s:
            await s.execute(text(
                "UPDATE memories SET retrieval_count = retrieval_count + 1, "
                "last_retrieved_at = :now, confidence = LEAST(:cap, confidence + :inc) "
                "WHERE tenant_id=:t AND memory_id = ANY(:ids)"),
                {"now": now, "cap": cap, "inc": inc, "t": tenant_id, "ids": memory_ids})

    async def list_memories(self, tenant_id, *, scope, status, tags, scope_ref, limit, cursor):
        conds = ["tenant_id=:t"]
        params = {"t": tenant_id, "lim": limit + 1}
        if scope:
            conds.append("scope=:sc")
            params["sc"] = scope
        if status:
            conds.append("status=:st")
            params["st"] = status
        if scope_ref:
            conds.append("scope_ref=:ref")
            params["ref"] = scope_ref
        if tags:
            conds.append("tags @> :tags")
            params["tags"] = tags
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        params["off"] = offset
        where = " AND ".join(conds)
        async with self._session(tenant_id) as s:
            rows = (await s.execute(text(
                f"SELECT {_MEM_COLS} FROM memories WHERE {where} "
                "ORDER BY created_at DESC, memory_id DESC OFFSET :off LIMIT :lim"),
                params)).fetchall()
        has_more = len(rows) > limit
        return Page(items=[_row_to_memory(r) for r in rows[:limit]],
                    next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
                    has_more=has_more)

    # ---- erasure ----
    async def delete_by_scope_ref(self, tenant_id, scope, scope_ref) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "DELETE FROM memories WHERE tenant_id=:t AND scope=:sc AND scope_ref=:ref"),
                {"t": tenant_id, "sc": scope, "ref": scope_ref})
            return res.rowcount or 0

    async def delete_by_provenance_user(self, tenant_id, user_id) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "DELETE FROM memories WHERE tenant_id=:t AND scope <> 'user' AND provenance @> "
                "CAST(:p AS jsonb)"),
                {"t": tenant_id, "p": json.dumps([{"user_id": user_id}])})
            return res.rowcount or 0

    async def count_provenance_user(self, tenant_id, user_id) -> int:
        # Mirror delete_by_provenance_user exactly (scope <> 'user') so the
        # erasure verification probe counts only rows the delete step removes.
        async with self._session(tenant_id) as s:
            return int((await s.execute(text(
                "SELECT count(*) FROM memories WHERE tenant_id=:t AND scope <> 'user' "
                "AND provenance @> CAST(:p AS jsonb)"),
                {"t": tenant_id, "p": json.dumps([{"user_id": user_id}])})).scalar() or 0)

    async def quarantine_by_run(self, tenant_id, run_id, now) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "UPDATE memories SET status='quarantined', updated_at=:now "
                "WHERE tenant_id=:t AND status='active' AND provenance @> CAST(:p AS jsonb)"),
                {"now": now, "t": tenant_id, "p": json.dumps([{"run_id": run_id}])})
            return res.rowcount or 0

    # ---- retention ----
    async def expire_past_ttl(self, tenant_id, now) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "UPDATE memories SET status='expired', updated_at=:now "
                "WHERE tenant_id=:t AND status='active' AND ttl_expires_at <= :now"),
                {"now": now, "t": tenant_id})
            return res.rowcount or 0

    async def hard_delete_expired(self, tenant_id, cutoff) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "DELETE FROM memories WHERE tenant_id=:t AND status='expired' "
                "AND updated_at <= :cut"), {"t": tenant_id, "cut": cutoff})
            return res.rowcount or 0

    async def revalidate(self, tenant_id, now, decay, expire_below,
                         revalidate_fraction) -> tuple[int, int]:
        decayed = expired = 0
        async with self._session(tenant_id) as s:
            rows = (await s.execute(text(
                f"SELECT {_MEM_COLS} FROM memories WHERE tenant_id=:t AND status='active' "
                "AND revalidate_at <= :now"), {"t": tenant_id, "now": now})).fetchall()
            for r in rows:
                rec = _row_to_memory(r)
                span = (rec.ttl_expires_at - now)
                if rec.retrieval_count == 0:
                    new_conf = max(0.0, rec.confidence - decay)
                    decayed += 1
                    if new_conf < expire_below:
                        expired += 1
                        await s.execute(text(
                            "UPDATE memories SET confidence=:c, status='expired', updated_at=:now "
                            "WHERE memory_id=:id AND tenant_id=:t"),
                            {"c": new_conf, "now": now, "id": rec.memory_id, "t": tenant_id})
                    else:
                        await s.execute(text(
                            "UPDATE memories SET confidence=:c, revalidate_at=:rv, updated_at=:now "
                            "WHERE memory_id=:id AND tenant_id=:t"),
                            {"c": new_conf, "rv": now + span * revalidate_fraction,
                             "now": now, "id": rec.memory_id, "t": tenant_id})
                else:
                    await s.execute(text(
                        "UPDATE memories SET revalidate_at=:rv, retrieval_count=0, updated_at=:now "
                        "WHERE memory_id=:id AND tenant_id=:t"),
                        {"rv": now + span * revalidate_fraction, "now": now,
                         "id": rec.memory_id, "t": tenant_id})
        return decayed, expired

    async def purge_quarantined(self, tenant_id, cutoff) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "DELETE FROM memories WHERE tenant_id=:t AND status='quarantined' "
                "AND updated_at <= :cut"), {"t": tenant_id, "cut": cutoff})
            return res.rowcount or 0

    # ---- chunks ----
    async def upsert_chunk(self, chunk) -> None:
        async with self._session(chunk.tenant_id) as s:
            await s.execute(text(
                "INSERT INTO rag_chunks (chunk_id, tenant_id, corpus_key, source_urn, "
                "chunk_seq, content, embedding, embedding_model_ver, snapshot_ver, "
                "source_updated_at, user_linkage, created_at) VALUES (:cid, :t, :ck, :su, "
                ":seq, :content, CAST(:emb AS vector), :ver, :snap, :sua, :ul, :created) "
                "ON CONFLICT (corpus_key, source_urn, chunk_seq, embedding_model_ver) "
                "DO UPDATE SET content=excluded.content, embedding=excluded.embedding, "
                "snapshot_ver=excluded.snapshot_ver, source_updated_at=excluded.source_updated_at, "
                "user_linkage=excluded.user_linkage"),
                {"cid": chunk.chunk_id, "t": chunk.tenant_id, "ck": chunk.corpus_key,
                 "su": chunk.source_urn, "seq": chunk.chunk_seq, "content": chunk.content,
                 "emb": _vec(chunk.embedding), "ver": chunk.embedding_model_ver,
                 "snap": chunk.snapshot_ver, "sua": chunk.source_updated_at,
                 "ul": chunk.user_linkage, "created": chunk.created_at or utcnow()})

    async def delete_chunks_by_source(self, tenant_id, corpus_key, source_urn) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "DELETE FROM rag_chunks WHERE tenant_id=:t AND corpus_key=:ck AND source_urn=:su"),
                {"t": tenant_id, "ck": corpus_key, "su": source_urn})
            return res.rowcount or 0

    async def delete_chunks_by_user(self, tenant_id, user_id) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "DELETE FROM rag_chunks WHERE tenant_id=:t AND user_linkage=:u"),
                {"t": tenant_id, "u": user_id})
            return res.rowcount or 0

    async def count_chunks_by_user(self, tenant_id, user_id) -> int:
        async with self._session(tenant_id) as s:
            return int((await s.execute(text(
                "SELECT count(*) FROM rag_chunks WHERE tenant_id=:t AND user_linkage=:u"),
                {"t": tenant_id, "u": user_id})).scalar() or 0)

    async def list_chunks(self, tenant_id, corpus_key, *, ver=None):
        async with self._session(tenant_id) as s:
            sql = ("SELECT chunk_id, tenant_id, corpus_key, source_urn, chunk_seq, content, "
                   "embedding, embedding_model_ver, snapshot_ver, source_updated_at, "
                   "user_linkage, created_at FROM rag_chunks WHERE tenant_id=:t AND corpus_key=:ck")
            params = {"t": tenant_id, "ck": corpus_key}
            if ver is not None:
                sql += " AND embedding_model_ver=:v"
                params["v"] = ver
            rows = (await s.execute(text(sql), params)).fetchall()
            return [_row_to_chunk(r) for r in rows]

    async def switch_embedding_ver(self, tenant_id, corpus_key, new_ver) -> int:
        async with self._session(tenant_id) as s:
            res = await s.execute(text(
                "DELETE FROM rag_chunks WHERE tenant_id=:t AND corpus_key=:ck "
                "AND embedding_model_ver <> :v"),
                {"t": tenant_id, "ck": corpus_key, "v": new_ver})
            return res.rowcount or 0

    async def search_chunks(self, tenant_id, corpora, embedding, top_k, *,
                            active_ver, snapshot_ver):
        results = []
        async with self._session(tenant_id) as s:
            for ck in corpora:
                params = {"t": tenant_id, "ck": ck, "v": active_ver.get(ck),
                          "emb": _vec(embedding), "k": top_k}
                snap = ""
                if snapshot_ver is not None:
                    snap = " AND snapshot_ver <= :snap"
                    params["snap"] = snapshot_ver
                rows = (await s.execute(text(
                    "SELECT chunk_id, tenant_id, corpus_key, source_urn, chunk_seq, content, "
                    "embedding, embedding_model_ver, snapshot_ver, source_updated_at, "
                    "user_linkage, created_at, 1 - (embedding <=> CAST(:emb AS vector)) AS sim "
                    "FROM rag_chunks WHERE tenant_id=:t AND corpus_key=:ck "
                    f"AND embedding_model_ver=:v AND embedding IS NOT NULL{snap} "
                    "ORDER BY embedding <=> CAST(:emb AS vector) ASC LIMIT :k"),
                    params)).fetchall()
                results.extend((_row_to_chunk(r), float(r._mapping["sim"])) for r in rows)
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    async def count_chunks(self, tenant_id, corpus_key) -> int:
        async with self._session(tenant_id) as s:
            return int((await s.execute(text(
                "SELECT count(*) FROM rag_chunks WHERE tenant_id=:t AND corpus_key=:ck"),
                {"t": tenant_id, "ck": corpus_key})).scalar() or 0)

    # ---- control-plane (public, RLS) ----
    async def get_corpus(self, tenant_id, corpus_key):
        async with self._session(tenant_id) as s:
            r = (await s.execute(text(
                "SELECT corpus_key, tenant_id, source, chunking, active_embedding_ver, refresh, "
                "anonymization_profile, status, created_at, updated_at FROM corpora "
                "WHERE tenant_id=:t AND corpus_key=:ck"),
                {"t": tenant_id, "ck": corpus_key})).first()
            return _row_to_corpus(r) if r else None

    async def list_corpora(self, tenant_id):
        async with self._session(tenant_id) as s:
            rows = (await s.execute(text(
                "SELECT corpus_key, tenant_id, source, chunking, active_embedding_ver, refresh, "
                "anonymization_profile, status, created_at, updated_at FROM corpora "
                "WHERE tenant_id=:t"), {"t": tenant_id})).fetchall()
            return [_row_to_corpus(r) for r in rows]

    async def upsert_corpus(self, corpus) -> None:
        async with self._session(corpus.tenant_id) as s:
            await s.execute(text(
                "INSERT INTO corpora (corpus_key, tenant_id, source, chunking, "
                "active_embedding_ver, refresh, anonymization_profile, status, created_at, "
                "updated_at) VALUES (:ck, :t, CAST(:src AS jsonb), CAST(:chunk AS jsonb), :ver, "
                "CAST(:refresh AS jsonb), CAST(:anon AS jsonb), :status, :created, :updated) "
                "ON CONFLICT (tenant_id, corpus_key) DO UPDATE SET source=excluded.source, "
                "chunking=excluded.chunking, active_embedding_ver=excluded.active_embedding_ver, "
                "refresh=excluded.refresh, anonymization_profile=excluded.anonymization_profile, "
                "status=excluded.status, updated_at=excluded.updated_at"),
                {"ck": corpus.corpus_key, "t": corpus.tenant_id, "src": json.dumps(corpus.source),
                 "chunk": json.dumps(corpus.chunking), "ver": corpus.active_embedding_ver,
                 "refresh": json.dumps(corpus.refresh),
                 "anon": json.dumps(corpus.anonymization_profile)
                 if corpus.anonymization_profile is not None else None,
                 "status": corpus.status, "created": corpus.created_at or utcnow(),
                 "updated": corpus.updated_at or utcnow()})

    async def get_policy(self, tenant_id):
        async with self._session(tenant_id) as s:
            r = (await s.execute(text(
                "SELECT tenant_id, ttl_overrides, pii_classes, injection_profile, corpus_flags, "
                "updated_at FROM tenant_policies WHERE tenant_id=:t"), {"t": tenant_id})).first()
            if r is None:
                return None
            m = r._mapping
            return TenantPolicy(
                tenant_id=str(m["tenant_id"]), ttl_overrides=_jsonify(m["ttl_overrides"]) or {},
                pii_classes=list(m["pii_classes"] or []), injection_profile=m["injection_profile"],
                corpus_flags=_jsonify(m["corpus_flags"]) or {}, updated_at=m["updated_at"])

    async def put_policy(self, policy) -> None:
        async with self._session(policy.tenant_id) as s:
            await s.execute(text(
                "INSERT INTO tenant_policies (tenant_id, ttl_overrides, pii_classes, "
                "injection_profile, corpus_flags, updated_at) VALUES (:t, CAST(:ttl AS jsonb), "
                ":pii, :prof, CAST(:cf AS jsonb), :now) ON CONFLICT (tenant_id) DO UPDATE SET "
                "ttl_overrides=excluded.ttl_overrides, pii_classes=excluded.pii_classes, "
                "injection_profile=excluded.injection_profile, corpus_flags=excluded.corpus_flags, "
                "updated_at=excluded.updated_at"),
                {"t": policy.tenant_id, "ttl": json.dumps(policy.ttl_overrides),
                 "pii": policy.pii_classes, "prof": policy.injection_profile,
                 "cf": json.dumps(policy.corpus_flags), "now": policy.updated_at or utcnow()})

    async def add_erasure(self, req) -> None:
        async with self._session(req.tenant_id) as s:
            await s.execute(text(
                "INSERT INTO erasure_requests (request_id, tenant_id, subject_type, subject_id, "
                "status, temporal_workflow_id, report, created_at, completed_at) VALUES "
                "(:rid, :t, :stype, :sid, :status, :wf, CAST(:report AS jsonb), :created, :done)"),
                _erasure_params(req))

    async def update_erasure(self, req) -> None:
        async with self._session(req.tenant_id) as s:
            await s.execute(text(
                "UPDATE erasure_requests SET status=:status, report=CAST(:report AS jsonb), "
                "completed_at=:done WHERE request_id=:rid AND tenant_id=:t"),
                _erasure_params(req))

    async def get_erasure(self, tenant_id, request_id):
        async with self._session(tenant_id) as s:
            r = (await s.execute(text(
                "SELECT request_id, tenant_id, subject_type, subject_id, status, "
                "temporal_workflow_id, report, created_at, completed_at FROM erasure_requests "
                "WHERE tenant_id=:t AND request_id=:rid"),
                {"t": tenant_id, "rid": request_id})).first()
            if r is None:
                return None
            m = r._mapping
            return ErasureRequest(
                request_id=str(m["request_id"]), tenant_id=str(m["tenant_id"]),
                subject_type=m["subject_type"], subject_id=m["subject_id"], status=m["status"],
                workflow_id=m["temporal_workflow_id"], report=_jsonify(m["report"]),
                created_at=m["created_at"], completed_at=m["completed_at"])

    async def _insert_audit(self, s, entry) -> None:
        await s.execute(text(
            "INSERT INTO write_audit (id, tenant_id, memory_id, action, actor, reason, "
            "trace_id, created_at) VALUES (:id, :t, :mid, :action, CAST(:actor AS jsonb), "
            ":reason, :trace, :created)"),
            {"id": entry.get("id") or new_id(), "t": entry["tenant_id"],
             "mid": entry.get("memory_id"), "action": entry["action"],
             "actor": json.dumps(entry.get("actor")), "reason": entry.get("reason"),
             "trace": entry.get("trace_id"), "created": entry.get("created_at") or utcnow()})

    async def add_audit(self, tenant_id, entry) -> None:
        async with self._session(tenant_id) as s:
            await self._insert_audit(s, entry)

    async def list_audit(self, tenant_id, memory_id):
        async with self._session(tenant_id) as s:
            rows = (await s.execute(text(
                "SELECT id, memory_id, action, actor, reason, trace_id, created_at "
                "FROM write_audit WHERE tenant_id=:t AND memory_id=:mid ORDER BY created_at"),
                {"t": tenant_id, "mid": memory_id})).fetchall()
            return [dict(r._mapping) for r in rows]

    async def _insert_outbox(self, s, tenant_id, topic, env) -> None:
        await s.execute(text(
            "INSERT INTO outbox (id, tenant_id, topic, event_type, payload, created_at) "
            "VALUES (:id, :t, :topic, :et, CAST(:payload AS jsonb), :created)"),
            {"id": new_id(), "t": tenant_id, "topic": topic, "et": env["event_type"],
             "payload": json.dumps(env, default=str), "created": utcnow()})

    async def add_outbox(self, tenant_id, topic, envelope) -> None:
        async with self._session(tenant_id) as s:
            await self._insert_outbox(s, tenant_id, topic, envelope)

    async def stats(self, tenant_id) -> dict:
        async with self._session(tenant_id) as s:
            by_scope = {}
            rows = (await s.execute(text(
                "SELECT scope, count(*) FROM memories WHERE tenant_id=:t AND status='active' "
                "GROUP BY scope"), {"t": tenant_id})).fetchall()
            for scope, n in rows:
                by_scope[scope] = int(n)
            q = int((await s.execute(text(
                "SELECT count(*) FROM memories WHERE tenant_id=:t AND status='quarantined'"),
                {"t": tenant_id})).scalar() or 0)
            chunks = int((await s.execute(text(
                "SELECT count(*) FROM rag_chunks WHERE tenant_id=:t"),
                {"t": tenant_id})).scalar() or 0)
            return {"active_by_scope": by_scope, "quarantined": q, "chunks": chunks}

    async def idempotency_get(self, tenant_id, key):
        async with self._session(tenant_id) as s:
            r = (await s.execute(text(
                "SELECT request_hash, status_code, response_body FROM idempotency_keys "
                "WHERE tenant_id=:t AND key=:k"), {"t": tenant_id, "k": key})).first()
            if r is None:
                return None
            m = r._mapping
            return {"request_hash": m["request_hash"], "status_code": m["status_code"],
                    "body": _jsonify(m["response_body"])}

    async def idempotency_put(self, tenant_id, key, request_hash, status, body) -> None:
        async with self._session(tenant_id) as s:
            await s.execute(text(
                "INSERT INTO idempotency_keys (tenant_id, key, request_hash, status_code, "
                "response_body, created_at) VALUES (:t, :k, :h, :sc, CAST(:b AS jsonb), :now) "
                "ON CONFLICT (tenant_id, key) DO NOTHING"),
                {"t": tenant_id, "k": key, "h": request_hash, "sc": status,
                 "b": json.dumps(body, default=str), "now": utcnow()})


def _row_to_corpus(r) -> Corpus:
    m = r._mapping
    return Corpus(
        corpus_key=m["corpus_key"], tenant_id=str(m["tenant_id"]), source=_jsonify(m["source"]),
        chunking=_jsonify(m["chunking"]), active_embedding_ver=m["active_embedding_ver"],
        refresh=_jsonify(m["refresh"]), anonymization_profile=_jsonify(m["anonymization_profile"]),
        status=m["status"], created_at=m["created_at"], updated_at=m["updated_at"])


def _erasure_params(req) -> dict:
    return {"rid": req.request_id, "t": req.tenant_id, "stype": req.subject_type,
            "sid": req.subject_id, "status": req.status, "wf": req.workflow_id,
            "report": json.dumps(req.report, default=str) if req.report else None,
            "created": req.created_at or utcnow(), "done": req.completed_at}


class OutboxDispatcher:
    """Polls unpublished outbox rows and publishes to the bus (MASTER-FR-034).
    Reads across tenants under the worker RLS policy."""

    def __init__(self, session_factory: async_sessionmaker, bus, batch_size: int = 100):
        self._sf = session_factory
        self._bus = bus
        self._batch = batch_size

    async def run_once(self) -> int:
        s = self._sf()
        try:
            await s.execute(text("SELECT set_config('app.worker', 'true', true)"))
            rows = (await s.execute(text(
                "SELECT id, topic, payload FROM outbox WHERE published_at IS NULL "
                "ORDER BY created_at ASC LIMIT :lim FOR UPDATE SKIP LOCKED"),
                {"lim": self._batch})).fetchall()
            for row in rows:
                payload = row._mapping["payload"]
                await self._bus.publish(row._mapping["topic"],
                                        payload if isinstance(payload, dict)
                                        else json.loads(payload))
            if rows:
                await s.execute(text(
                    "UPDATE outbox SET published_at=now() WHERE id = ANY(:ids)"),
                    {"ids": [r._mapping["id"] for r in rows]})
            await s.commit()
            return len(rows)
        finally:
            await s.close()


class SqlDedupStore:
    """Durable consumer dedup on processed_events (Redis is the runtime default)."""

    def __init__(self, session_factory: async_sessionmaker):
        self._sf = session_factory

    async def seen(self, tenant_id: str, event_id: str) -> bool:
        s = self._sf()
        try:
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                            {"t": tenant_id})
            inserted = (await s.execute(text(
                "INSERT INTO processed_events (event_id, tenant_id, created_at) "
                "VALUES (:e, :t, now()) ON CONFLICT (event_id) DO NOTHING RETURNING event_id"),
                {"e": event_id, "t": tenant_id})).scalar()
            await s.commit()
            return inserted is None
        finally:
            await s.close()

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        return False

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        return None


_ = (STATUS_ACTIVE, STATUS_EXPIRED, STATUS_QUARANTINED, datetime, recency_decay)
