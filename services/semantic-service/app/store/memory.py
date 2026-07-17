"""In-memory store: unit-tier implementation of the repository ports.

Acts as the in-memory tenant-policy fake required by CONVENTIONS.md — every
repo method is scoped to the UoW's tenant, so cross-tenant reads return
nothing, mirroring Postgres RLS behavior.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
from collections import defaultdict

from app.domain.entities import (
    ChartRef,
    CompileLogEntry,
    ModelVersion,
    Operation,
    SemanticModel,
    VerifiedQuery,
)
from app.domain.ports import Page
from app.utils import decode_cursor, encode_cursor


class MemoryState:
    """Shared backing store across units of work (one per process/test app)."""

    def __init__(self):
        self.models: dict[str, SemanticModel] = {}
        self.versions: dict[str, ModelVersion] = {}
        self.verified_queries: dict[str, VerifiedQuery] = {}
        self.compile_log: list[CompileLogEntry] = []
        self.operations: dict[str, Operation] = {}
        self.chart_refs: dict[tuple[str, str], ChartRef] = {}
        self.projections: dict[str, dict] = {}  # version_id -> definition snapshot
        self.outbox: list[tuple[str, dict]] = []
        self.idempotency: dict[tuple[str, str], dict] = {}
        self.model_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def events_of_type(self, event_type: str) -> list[dict]:
        return [e for _, e in self.outbox if e["event_type"] == event_type]


def _copy(entity):
    return dataclasses.replace(entity) if entity is not None else None


def _paginate(items: list, limit: int, cursor: str | None) -> Page:
    offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
    window = items[offset: offset + limit]
    has_more = offset + limit < len(items)
    return Page(items=window,
                next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
                has_more=has_more)


class MemoryModelRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    def _visible(self, m: SemanticModel | None,
                 include_deleted: bool = False) -> SemanticModel | None:
        if m is None or m.tenant_id != self.tenant_id:
            return None  # policy fake: cross-tenant rows do not exist
        if m.deleted_at and not include_deleted:
            return None
        return m

    async def add(self, model: SemanticModel) -> None:
        self.state.models[model.id] = _copy(model)

    async def get(self, model_id: str, include_deleted: bool = False) -> SemanticModel | None:
        return _copy(self._visible(self.state.models.get(model_id), include_deleted))

    async def get_by_name(self, workspace_id: str, name: str) -> SemanticModel | None:
        for m in self.state.models.values():
            if (self._visible(m) and m.workspace_id == workspace_id
                    and m.name.lower() == name.lower()):
                return _copy(m)
        return None

    async def update(self, model: SemanticModel) -> None:
        current = self.state.models.get(model.id)
        if current and current.tenant_id == self.tenant_id:
            self.state.models[model.id] = _copy(model)

    async def list(self, workspace_id: str | None, limit: int,
                   cursor: str | None) -> Page:
        rows = [m for m in self.state.models.values() if self._visible(m)
                and (workspace_id is None or m.workspace_id == workspace_id)]
        rows.sort(key=lambda m: (m.created_at, m.id), reverse=True)
        page = _paginate(rows, limit, cursor)
        page.items = [_copy(m) for m in page.items]
        return page

    async def all_active(self) -> list[SemanticModel]:
        return [_copy(m) for m in self.state.models.values() if self._visible(m)]


class MemoryVersionRepo:
    def __init__(self, state: MemoryState, tenant_id: str, held_locks: list):
        self.state, self.tenant_id = state, tenant_id
        self._held_locks = held_locks

    def _mine(self, v: ModelVersion | None) -> ModelVersion | None:
        return v if v is not None and v.tenant_id == self.tenant_id else None

    async def add(self, version: ModelVersion) -> None:
        self.state.versions[version.id] = _copy(version)

    async def get(self, model_id: str, version_no: int) -> ModelVersion | None:
        for v in self.state.versions.values():
            if self._mine(v) and v.model_id == model_id and v.version_no == version_no:
                return _copy(v)
        return None

    async def get_by_id(self, version_id: str) -> ModelVersion | None:
        return _copy(self._mine(self.state.versions.get(version_id)))

    async def latest(self, model_id: str) -> ModelVersion | None:
        mine = [v for v in self.state.versions.values()
                if self._mine(v) and v.model_id == model_id]
        return _copy(max(mine, key=lambda v: v.version_no)) if mine else None

    async def open_version(self, model_id: str) -> ModelVersion | None:
        for v in self.state.versions.values():
            if (self._mine(v) and v.model_id == model_id
                    and v.status in ("draft", "in_review", "rejected")):
                return _copy(v)
        return None

    async def list(self, model_id: str, limit: int, cursor: str | None) -> Page:
        mine = sorted(
            (v for v in self.state.versions.values()
             if self._mine(v) and v.model_id == model_id),
            key=lambda v: -v.version_no)
        page = _paginate(mine, limit, cursor)
        page.items = [_copy(v) for v in page.items]
        return page

    async def update(self, version: ModelVersion) -> None:
        current = self.state.versions.get(version.id)
        if current and current.tenant_id == self.tenant_id:
            self.state.versions[version.id] = _copy(version)

    async def lock_model(self, model_id: str) -> None:
        # asyncio.Lock stands in for the pg advisory xact lock (BR-10): acquired
        # here, held until the unit of work exits (like a transaction-scoped
        # advisory lock), so concurrent approvals serialize.
        lock = self.state.model_locks[model_id]
        await lock.acquire()
        self._held_locks.append(lock)

    async def rebuild_projections(self, version: ModelVersion) -> None:
        self.state.projections[version.id] = dict(version.definition)


class MemoryVerifiedQueryRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    def _mine(self, vq: VerifiedQuery | None) -> VerifiedQuery | None:
        if vq is None or vq.tenant_id != self.tenant_id or vq.deleted_at:
            return None
        return vq

    async def add(self, vq: VerifiedQuery) -> None:
        self.state.verified_queries[vq.id] = _copy(vq)

    async def get(self, vq_id: str) -> VerifiedQuery | None:
        return _copy(self._mine(self.state.verified_queries.get(vq_id)))

    async def update(self, vq: VerifiedQuery) -> None:
        current = self.state.verified_queries.get(vq.id)
        if current and current.tenant_id == self.tenant_id:
            self.state.verified_queries[vq.id] = _copy(vq)

    async def list(self, workspace_id: str | None, status: str | None,
                   limit: int, cursor: str | None) -> Page:
        rows = [vq for vq in self.state.verified_queries.values() if self._mine(vq)
                and (workspace_id is None or vq.workspace_id == workspace_id)
                and (status is None or vq.status == status)]
        rows.sort(key=lambda vq: (vq.created_at, vq.id), reverse=True)
        page = _paginate(rows, limit, cursor)
        page.items = [_copy(vq) for vq in page.items]
        return page

    async def search(self, workspace_id: str, embedding: list[float],
                     top_k: int) -> list[tuple[VerifiedQuery, float]]:
        # BR-14: tenant+workspace+approved are hard predicates, never relaxed
        candidates = [
            vq for vq in self.state.verified_queries.values()
            if self._mine(vq) and vq.workspace_id == workspace_id
            and vq.status == "approved" and vq.embedding
        ]
        scored = [(_copy(vq), _cosine(embedding, vq.embedding)) for vq in candidates]
        scored.sort(key=lambda pair: (-pair[1], pair[0].id))
        return scored[:top_k]

    async def approved_for_model(self, model_id: str) -> list[VerifiedQuery]:
        return [_copy(vq) for vq in self.state.verified_queries.values()
                if self._mine(vq) and vq.model_id == model_id
                and vq.status == "approved"]

    async def approved_all(self) -> list[VerifiedQuery]:
        return [_copy(vq) for vq in self.state.verified_queries.values()
                if self._mine(vq) and vq.status == "approved"]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class MemoryCompileLogRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    async def add(self, entry: CompileLogEntry) -> None:
        self.state.compile_log.append(_copy(entry))


class MemoryOperationRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    async def add(self, op: Operation) -> None:
        self.state.operations[op.id] = _copy(op)

    async def get(self, op_id: str) -> Operation | None:
        op = self.state.operations.get(op_id)
        return _copy(op) if op and op.tenant_id == self.tenant_id else None

    async def update(self, op: Operation) -> None:
        current = self.state.operations.get(op.id)
        if current and current.tenant_id == self.tenant_id:
            self.state.operations[op.id] = _copy(op)


class MemoryChartRefRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    async def upsert(self, ref: ChartRef) -> None:
        self.state.chart_refs[(ref.tenant_id, ref.chart_urn)] = _copy(ref)

    async def charts_referencing(self, measure: str) -> list[ChartRef]:
        return [_copy(r) for (tid, _), r in self.state.chart_refs.items()
                if tid == self.tenant_id and measure in r.measures]


class MemoryOutboxRepo:
    def __init__(self, state: MemoryState, staged: list):
        self.state, self.staged = state, staged

    async def add(self, topic: str, envelope: dict) -> None:
        self.staged.append((topic, envelope))


class MemoryIdempotencyRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    async def get(self, key: str) -> dict | None:
        return self.state.idempotency.get((self.tenant_id, key))

    async def put(self, key: str, request_hash: str, status_code: int, body: dict) -> None:
        self.state.idempotency[(self.tenant_id, key)] = {
            "request_hash": request_hash, "status_code": status_code, "body": body,
        }


class MemoryUnitOfWork:
    """Mutations apply immediately (unit-tier simplification); outbox entries are
    staged and flushed on commit so tests still observe emit-after-commit order."""

    def __init__(self, state: MemoryState, tenant_id: str):
        self.tenant_id = tenant_id
        self._staged_outbox: list[tuple[str, dict]] = []
        self._held_locks: list[asyncio.Lock] = []
        self.models = MemoryModelRepo(state, tenant_id)
        self.versions = MemoryVersionRepo(state, tenant_id, self._held_locks)
        self.verified_queries = MemoryVerifiedQueryRepo(state, tenant_id)
        self.compile_log = MemoryCompileLogRepo(state, tenant_id)
        self.operations = MemoryOperationRepo(state, tenant_id)
        self.chart_refs = MemoryChartRefRepo(state, tenant_id)
        self.outbox = MemoryOutboxRepo(state, self._staged_outbox)
        self.idempotency = MemoryIdempotencyRepo(state, tenant_id)
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                await self.commit()
        finally:
            self._release_locks()

    def _release_locks(self):
        while self._held_locks:
            self._held_locks.pop().release()

    async def commit(self):
        self._state.outbox.extend(self._staged_outbox)
        self._staged_outbox.clear()

    async def rollback(self):
        self._staged_outbox.clear()


def memory_uow_factory(state: MemoryState):
    def factory(tenant_id: str) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(state, tenant_id)

    return factory
