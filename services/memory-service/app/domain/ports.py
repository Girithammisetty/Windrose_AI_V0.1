"""Ports (Protocols) + shared value objects wiring domain to adapters/stores.

The store interface is deliberately store-agnostic (MEM §8 Qdrant upgrade path):
both the SQL (pgvector) store and the in-memory unit double implement it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.domain.entities import (
    Corpus,
    ErasureRequest,
    MemoryRecord,
    RagChunk,
    TenantPolicy,
)


@dataclass
class Page:
    items: list
    next_cursor: str | None
    has_more: bool


@dataclass
class CallCtx:
    tenant_id: str
    actor: dict
    via_agent: dict | None = None
    trace_id: str | None = None
    subject: str = ""
    typ: str = "user"
    obo_sub: str | None = None
    scopes: list[str] = field(default_factory=list)
    agent_id: str | None = None
    agent_version: str | None = None


class Embedder(Protocol):
    async def embed(self, tenant_id: str, text: str) -> list[float]: ...


class InjectionScreener(Protocol):
    async def score(self, tenant_id: str, text: str) -> float:
        """Returns a poisoning/injection score in [0,1]. Raises on unavailability
        so the write path can fail closed (BR-1)."""
        ...


class PiiScanner(Protocol):
    async def scan(self, text: str, classes: list[str]) -> list[str]:
        """Returns the subset of disallowed PII classes found in text."""
        ...


class Anonymizer(Protocol):
    async def anonymize(self, text: str, profile: dict | None) -> str: ...


class PendingQueue(Protocol):
    """Embedding-outage write queue (BR-2)."""

    async def enqueue(self, entry: dict) -> None: ...
    async def list_all(self, tenant_id: str) -> list[dict]: ...
    async def remove(self, tenant_id: str, entry_id: str) -> None: ...


class SessionStore(Protocol):
    async def put(self, tenant_id: str, session_id: str, entry_id: str, value: dict) -> None: ...
    async def list(self, tenant_id: str, session_id: str) -> list[dict]: ...
    async def wipe(self, tenant_id: str, session_id: str) -> int: ...
    async def scan_subject(self, tenant_id: str, subject_id: str) -> int: ...
    async def purge_subject(self, tenant_id: str, subject_id: str) -> int: ...


class MemoryStore(Protocol):
    """Per-tenant memory + chunk + control-plane store."""

    # provisioning (control-plane, privileged)
    async def provision_tenant(self, tenant_id: str) -> None: ...
    async def drop_tenant(self, tenant_id: str) -> None: ...
    async def tenant_ready(self, tenant_id: str) -> bool: ...
    async def ping(self) -> bool:
        """Liveness probe for /readyz: verifies the store backend is reachable."""
        ...

    # memories
    async def commit_write(
        self, rec: MemoryRecord, *, evicted: list[MemoryRecord], audit: list[dict],
        envelopes: list[tuple[str, dict]], is_update: bool = False,
    ) -> None:
        """Atomic (single-txn) upsert of ``rec`` + expire ``evicted`` + append
        ``audit`` rows + enqueue ``envelopes`` to the outbox (MASTER-FR-034)."""
        ...
    async def hard_delete_memory(self, tenant_id: str, memory_id: str) -> None: ...
    async def get_memory(self, tenant_id: str, memory_id: str) -> MemoryRecord | None: ...
    async def find_similar(
        self, tenant_id: str, scope: str, scope_ref: str, embedding: list[float], threshold: float
    ) -> tuple[MemoryRecord, float] | None: ...
    async def search_memories(
        self,
        tenant_id: str,
        scopes: list[tuple[str, str]],
        embedding: list[float] | None,
        top_k: int,
        *,
        min_confidence: float | None,
        tags: list[str] | None,
    ) -> list[tuple[MemoryRecord, float]]: ...
    async def count_active(self, tenant_id: str, scope: str, scope_ref: str) -> int: ...
    async def eviction_candidate(
        self, tenant_id: str, scope: str, scope_ref: str, now: datetime,
        half_life_seconds: float, skip_after: datetime,
    ) -> MemoryRecord | None: ...
    async def bump_retrieval(
        self, tenant_id: str, memory_ids: list[str], now: datetime, inc: float, cap: float
    ) -> None: ...
    async def list_memories(
        self, tenant_id: str, *, scope: str | None, status: str | None,
        tags: list[str] | None, scope_ref: str | None, limit: int, cursor: str | None,
    ) -> Page: ...

    # erasure support. delete_by_provenance_user and count_provenance_user MUST
    # cover the same set (non-user-scope, provenance user_id match) so the
    # verification probe never counts rows the delete step didn't remove.
    async def delete_by_scope_ref(self, tenant_id: str, scope: str, scope_ref: str) -> int: ...
    async def delete_by_provenance_user(self, tenant_id: str, user_id: str) -> int: ...
    async def count_provenance_user(self, tenant_id: str, user_id: str) -> int: ...
    async def quarantine_by_run(self, tenant_id: str, run_id: str, now: datetime) -> int: ...

    # retention
    async def expire_past_ttl(self, tenant_id: str, now: datetime) -> int: ...
    async def hard_delete_expired(self, tenant_id: str, cutoff: datetime) -> int: ...
    async def revalidate(
        self, tenant_id: str, now: datetime, decay: float, expire_below: float,
        revalidate_fraction: float,
    ) -> tuple[int, int]: ...
    async def purge_quarantined(self, tenant_id: str, cutoff: datetime) -> int: ...

    # rag chunks
    async def upsert_chunk(self, chunk: RagChunk) -> None: ...
    async def delete_chunks_by_source(
        self, tenant_id: str, corpus_key: str, source_urn: str
    ) -> int: ...
    async def delete_chunks_by_user(self, tenant_id: str, user_id: str) -> int: ...
    async def count_chunks_by_user(self, tenant_id: str, user_id: str) -> int: ...
    async def list_chunks(
        self, tenant_id: str, corpus_key: str, *, ver: str | None = None
    ) -> list[RagChunk]: ...
    async def switch_embedding_ver(
        self, tenant_id: str, corpus_key: str, new_ver: str
    ) -> int: ...
    async def search_chunks(
        self, tenant_id: str, corpora: list[str], embedding: list[float], top_k: int,
        *, active_ver: dict[str, str], snapshot_ver: str | None,
    ) -> list[tuple[RagChunk, float]]: ...
    async def count_chunks(self, tenant_id: str, corpus_key: str) -> int: ...

    # control-plane (public, RLS)
    async def get_corpus(self, tenant_id: str, corpus_key: str) -> Corpus | None: ...
    async def list_corpora(self, tenant_id: str) -> list[Corpus]: ...
    async def upsert_corpus(self, corpus: Corpus) -> None: ...
    async def get_policy(self, tenant_id: str) -> TenantPolicy | None: ...
    async def put_policy(self, policy: TenantPolicy) -> None: ...
    async def add_erasure(self, req: ErasureRequest) -> None: ...
    async def update_erasure(self, req: ErasureRequest) -> None: ...
    async def get_erasure(self, tenant_id: str, request_id: str) -> ErasureRequest | None: ...
    async def add_audit(self, tenant_id: str, entry: dict) -> None: ...
    async def list_audit(self, tenant_id: str, memory_id: str) -> list[dict]: ...
    async def add_outbox(self, tenant_id: str, topic: str, envelope: dict) -> None: ...
    async def stats(self, tenant_id: str) -> dict: ...
    async def idempotency_get(self, tenant_id: str, key: str) -> dict | None: ...
    async def idempotency_put(
        self, tenant_id: str, key: str, request_hash: str, status: int, body: dict
    ) -> None: ...


class MembershipChecker(Protocol):
    async def is_member(self, tenant_id: str, user_id: str, workspace_id: str) -> bool: ...


@dataclass
class ServiceDeps:
    settings: Any
    clock: Any
    store: Any
    embedder: Embedder
    screener: InjectionScreener
    pii: PiiScanner
    anonymizer: Anonymizer
    session_store: SessionStore
    membership: MembershipChecker
    pending: PendingQueue
    # In-process cache of tenants confirmed provisioned (schema + defaults),
    # shared by the API services and the event consumer so the lazy
    # ensure-on-first-use path (BR-14 fallback) checks the store once per tenant.
    provisioned_tenants: set = field(default_factory=set)
