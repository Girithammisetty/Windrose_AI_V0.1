"""In-memory store (unit tier / dev): tenant-scoped repos over shared state,
with a tenant-policy fake mirroring RLS semantics (rows outside uow.tenant_id
are invisible). The outbox publishes to the in-memory bus on commit —
functionally the poller (MASTER-FR-034)."""

from __future__ import annotations

import dataclasses
from datetime import datetime

from app.adapters.embeddings import cosine
from app.domain.entities import (
    Budget,
    CacheEntry,
    GuardrailPolicy,
    ModelLadder,
    ProviderDeployment,
    RequestLog,
    TenantConfig,
    VirtualKey,
)
from app.domain.ports import Page
from app.utils import decode_cursor, encode_cursor, utcnow


class MemoryState:
    def __init__(self, bus=None):
        self.bus = bus
        self.providers: dict[str, ProviderDeployment] = {}
        self.ladders: dict[tuple[str, str, str], ModelLadder] = {}  # (tenant, class, scope)
        self.budgets: dict[str, Budget] = {}
        self.keys: dict[str, VirtualKey] = {}
        self.policies: dict[str, GuardrailPolicy] = {}  # tenant -> current policy
        self.request_log: dict[str, RequestLog] = {}
        self.cache_entries: dict[str, CacheEntry] = {}
        self.tenant_configs: dict[str, TenantConfig] = {}
        self.idempotency: dict[tuple[str, str], dict] = {}
        self.outbox_published: list[tuple[str, dict]] = []

    def owner_of(self, collection: str, item_id: str) -> str | None:
        """Global visibility for the cross-tenant audit probe (MASTER-FR-003);
        RLS makes this impossible in sql mode, where the probe returns None."""
        item = getattr(self, collection).get(item_id)
        return item.tenant_id if item else None


def _clone(entity):
    return dataclasses.replace(entity)


class _TenantRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id

    def _visible(self, entity) -> bool:
        return entity.tenant_id == self.tenant_id


class MemoryProviderRepo(_TenantRepo):
    async def add(self, d: ProviderDeployment) -> None:
        self.state.providers[d.id] = _clone(d)

    async def get(self, deployment_id: str) -> ProviderDeployment | None:
        d = self.state.providers.get(deployment_id)
        return _clone(d) if d and self._visible(d) and d.deleted_at is None else None

    async def update(self, d: ProviderDeployment) -> None:
        if d.id in self.state.providers and self._visible(self.state.providers[d.id]):
            self.state.providers[d.id] = _clone(d)

    async def list(self, limit: int, cursor: str | None) -> Page:
        rows = sorted(
            (d for d in self.state.providers.values()
             if self._visible(d) and d.deleted_at is None),
            key=lambda d: d.id,
        )
        return _paginate(rows, limit, cursor)

    async def list_all_active_or_draining(self) -> list[ProviderDeployment]:
        return [
            _clone(d) for d in self.state.providers.values()
            if self._visible(d) and d.deleted_at is None
            and d.status in ("active", "draining")
        ]

    async def count_active_for_alias(self, model_alias: str,
                                     exclude_id: str | None = None) -> int:
        return sum(
            1 for d in self.state.providers.values()
            if self._visible(d) and d.deleted_at is None and d.status == "active"
            and d.model_family == model_alias and d.id != exclude_id
        )


class MemoryLadderRepo(_TenantRepo):
    async def get(self, request_class: str, scope: str) -> ModelLadder | None:
        ladder = self.state.ladders.get((self.tenant_id, request_class, scope))
        return _clone(ladder) if ladder and ladder.deleted_at is None else None

    async def upsert(self, ladder: ModelLadder) -> ModelLadder:
        self.state.ladders[(self.tenant_id, ladder.request_class, ladder.scope)] = (
            _clone(ladder)
        )
        return ladder


class MemoryBudgetRepo(_TenantRepo):
    async def add(self, b: Budget) -> None:
        self.state.budgets[b.id] = _clone(b)

    async def get(self, budget_id: str) -> Budget | None:
        b = self.state.budgets.get(budget_id)
        return _clone(b) if b and self._visible(b) and b.deleted_at is None else None

    async def update(self, b: Budget) -> None:
        if b.id in self.state.budgets and self._visible(self.state.budgets[b.id]):
            self.state.budgets[b.id] = _clone(b)

    async def list(self, limit: int, cursor: str | None,
                   scope_type: str | None = None) -> Page:
        rows = sorted(
            (b for b in self.state.budgets.values()
             if self._visible(b) and b.deleted_at is None
             and (scope_type is None or b.scope_type == scope_type)),
            key=lambda b: b.id,
        )
        return _paginate(rows, limit, cursor)

    async def for_scope(self, scope_type: str, scope_ref: str) -> list[Budget]:
        return [
            _clone(b) for b in self.state.budgets.values()
            if self._visible(b) and b.deleted_at is None
            and b.scope_type == scope_type and b.scope_ref == scope_ref
        ]


class MemoryKeyRepo(_TenantRepo):
    async def add(self, k: VirtualKey) -> None:
        self.state.keys[k.id] = _clone(k)

    async def get(self, key_id: str) -> VirtualKey | None:
        k = self.state.keys.get(key_id)
        return _clone(k) if k and self._visible(k) else None

    async def get_by_hash_any_tenant(self, key_hash: str) -> VirtualKey | None:
        # Deliberately global: virtual keys authenticate before the tenant is
        # known (the JWT tenant must then match). In sql mode this uses the
        # dedicated authenticator policy on key_hash.
        for k in self.state.keys.values():
            if k.key_hash == key_hash:
                return _clone(k)
        return None

    async def update(self, k: VirtualKey) -> None:
        if k.id in self.state.keys and self._visible(self.state.keys[k.id]):
            self.state.keys[k.id] = _clone(k)

    async def list(self, limit: int, cursor: str | None) -> Page:
        rows = sorted(
            (k for k in self.state.keys.values() if self._visible(k)),
            key=lambda k: k.id,
        )
        return _paginate(rows, limit, cursor)

    async def list_active(self) -> list[VirtualKey]:
        return [
            _clone(k) for k in self.state.keys.values()
            if self._visible(k) and k.status == "active"
        ]


class MemoryPolicyRepo(_TenantRepo):
    async def current(self) -> GuardrailPolicy | None:
        p = self.state.policies.get(self.tenant_id)
        return _clone(p) if p else None

    async def put(self, policy: GuardrailPolicy) -> GuardrailPolicy:
        self.state.policies[self.tenant_id] = _clone(policy)
        return policy


class MemoryRequestLogRepo(_TenantRepo):
    async def add(self, entry: RequestLog) -> None:
        self.state.request_log[entry.request_id] = _clone(entry)

    async def get(self, request_id: str) -> RequestLog | None:
        e = self.state.request_log.get(request_id)
        return _clone(e) if e and self._visible(e) else None

    async def aggregate_costs(self, since) -> list[dict]:
        groups: dict[tuple, dict] = {}
        for e in self.state.request_log.values():
            if not self._visible(e):
                continue
            if e.created_at is not None and since is not None and e.created_at < since:
                continue
            key = (e.deployment_id, e.model_alias, e.request_class, bool(e.cached))
            row = groups.get(key)
            if row is None:
                row = {
                    "deployment_id": e.deployment_id, "model_alias": e.model_alias,
                    "request_class": e.request_class, "cached": bool(e.cached),
                    "requests": 0, "input_tokens": 0, "output_tokens": 0,
                    "cost_usd": 0.0,
                }
                groups[key] = row
            row["requests"] += 1
            row["input_tokens"] += int(e.input_tokens or 0)
            row["output_tokens"] += int(e.output_tokens or 0)
            row["cost_usd"] += float(e.cost_usd or 0.0)
        return list(groups.values())


class MemoryCacheEntryRepo(_TenantRepo):
    async def add(self, entry: CacheEntry) -> None:
        self.state.cache_entries[entry.id] = _clone(entry)

    async def search(self, context_hash: str, embedding: list[float],
                     threshold: float, now: datetime) -> CacheEntry | None:
        best, best_sim = None, 0.0
        for e in self.state.cache_entries.values():
            if not self._visible(e) or e.context_hash != context_hash:
                continue
            if e.expires_at <= now or e.embedding is None:
                continue
            sim = cosine(e.embedding, embedding)
            if sim >= threshold and sim > best_sim:
                best, best_sim = e, sim
        return _clone(best) if best else None

    async def purge(self, workspace_id: str | None = None) -> int:
        doomed = [
            e.id for e in self.state.cache_entries.values()
            if self._visible(e)
            and (workspace_id is None or e.workspace_id == workspace_id)
        ]
        for eid in doomed:
            del self.state.cache_entries[eid]
        return len(doomed)


class MemoryTenantConfigRepo(_TenantRepo):
    async def get(self, tenant_id: str) -> TenantConfig | None:
        cfg = self.state.tenant_configs.get(tenant_id)
        return dataclasses.replace(cfg) if cfg else None

    async def put(self, cfg: TenantConfig) -> None:
        self.state.tenant_configs[cfg.tenant_id] = dataclasses.replace(cfg)


class MemoryOutboxRepo(_TenantRepo):
    def __init__(self, state: MemoryState, tenant_id: str, staged: list):
        super().__init__(state, tenant_id)
        self._staged = staged

    async def add(self, topic: str, envelope: dict) -> None:
        self._staged.append((topic, envelope))


class MemoryIdempotencyRepo(_TenantRepo):
    async def get(self, key: str) -> dict | None:
        return self.state.idempotency.get((self.tenant_id, key))

    async def put(self, key: str, request_hash: str, status_code: int,
                  body: dict) -> None:
        self.state.idempotency[(self.tenant_id, key)] = {
            "request_hash": request_hash,
            "status_code": status_code,
            "body": body,
            "created_at": utcnow(),
        }


class MemoryUnitOfWork:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id
        self._staged_outbox: list[tuple[str, dict]] = []
        self.providers = MemoryProviderRepo(state, tenant_id)
        self.ladders = MemoryLadderRepo(state, tenant_id)
        self.budgets = MemoryBudgetRepo(state, tenant_id)
        self.keys = MemoryKeyRepo(state, tenant_id)
        self.policies = MemoryPolicyRepo(state, tenant_id)
        self.request_log = MemoryRequestLogRepo(state, tenant_id)
        self.cache_entries = MemoryCacheEntryRepo(state, tenant_id)
        self.tenant_configs = MemoryTenantConfigRepo(state, tenant_id)
        self.outbox = MemoryOutboxRepo(state, tenant_id, self._staged_outbox)
        self.idempotency = MemoryIdempotencyRepo(state, tenant_id)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            await self.commit()

    async def commit(self) -> None:
        for topic, envelope in self._staged_outbox:
            self.state.outbox_published.append((topic, envelope))
            if self.state.bus is not None:
                await self.state.bus.publish(topic, envelope)
        self._staged_outbox.clear()

    async def rollback(self) -> None:
        self._staged_outbox.clear()


def memory_uow_factory(state: MemoryState):
    def factory(tenant_id: str) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(state, tenant_id)

    return factory


def _paginate(rows: list, limit: int, cursor: str | None) -> Page:
    start = 0
    if cursor:
        after = decode_cursor(cursor).get("after")
        for i, row in enumerate(rows):
            if row.id > after:
                start = i
                break
        else:
            start = len(rows)
    window = rows[start:start + limit]
    has_more = start + limit < len(rows)
    return Page(
        data=[_clone(r) for r in window],
        next_cursor=encode_cursor({"after": window[-1].id}) if has_more and window else None,
        has_more=has_more,
    )
