"""SQL repositories + unit of work (integration/prod).

Every tenant UoW opens a transaction and sets `app.tenant_id` so Postgres RLS
(MASTER-FR-001) applies to the non-privileged application role. The virtual-key
authenticator and the outbox dispatcher use dedicated GUC-scoped policies."""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
from app.store.orm import (
    BudgetRow,
    GuardrailPolicyRow,
    IdempotencyKeyRow,
    ModelLadderRow,
    OutboxRow,
    ProcessedEventRow,
    ProviderDeploymentRow,
    RequestLogRow,
    SemanticCacheEntryRow,
    TenantConfigRow,
    VirtualKeyRow,
)
from app.utils import decode_cursor, encode_cursor, utcnow, uuid7


def make_engine(database_url: str):
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )


def _fields(cls):
    return [f.name for f in dataclasses.fields(cls)]


_PROVIDER_FIELDS = _fields(ProviderDeployment)
_LADDER_FIELDS = _fields(ModelLadder)
_BUDGET_FIELDS = _fields(Budget)
_KEY_FIELDS = _fields(VirtualKey)
_LOG_FIELDS = _fields(RequestLog)
_CFG_FIELDS = _fields(TenantConfig)


def _to_entity(row, fields, cls):
    return cls(**{f: getattr(row, f) for f in fields})


def _apply(row, entity, fields):
    for f in fields:
        setattr(row, f, getattr(entity, f))


def _budget_entity(row: BudgetRow) -> Budget:
    b = _to_entity(row, _BUDGET_FIELDS, Budget)
    b.limit_usd = float(b.limit_usd)
    return b


async def _page(session: AsyncSession, stmt, limit: int, cursor: str | None,
                to_entity) -> Page:
    if cursor:
        stmt = stmt.where(text("id > :after")).params(after=decode_cursor(cursor)["after"])
    rows = (await session.execute(stmt.limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page(
        data=[to_entity(r) for r in rows],
        next_cursor=encode_cursor({"after": rows[-1].id}) if has_more and rows else None,
        has_more=has_more,
    )


class SqlProviderRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, d: ProviderDeployment) -> None:
        row = ProviderDeploymentRow()
        _apply(row, d, _PROVIDER_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, deployment_id: str) -> ProviderDeployment | None:
        row = await self.s.get(ProviderDeploymentRow, deployment_id)
        if row is None or row.deleted_at is not None:
            return None
        return _to_entity(row, _PROVIDER_FIELDS, ProviderDeployment)

    async def update(self, d: ProviderDeployment) -> None:
        row = await self.s.get(ProviderDeploymentRow, d.id)
        if row is not None:
            _apply(row, d, _PROVIDER_FIELDS)
            await self.s.flush()

    async def list(self, limit: int, cursor: str | None) -> Page:
        stmt = (
            select(ProviderDeploymentRow)
            .where(ProviderDeploymentRow.deleted_at.is_(None))
            .order_by(ProviderDeploymentRow.id)
        )
        return await _page(
            self.s, stmt, limit, cursor,
            lambda r: _to_entity(r, _PROVIDER_FIELDS, ProviderDeployment),
        )

    async def list_all_active_or_draining(self) -> list[ProviderDeployment]:
        stmt = select(ProviderDeploymentRow).where(
            ProviderDeploymentRow.deleted_at.is_(None),
            ProviderDeploymentRow.status.in_(["active", "draining"]),
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _PROVIDER_FIELDS, ProviderDeployment) for r in rows]

    async def count_active_for_alias(self, model_alias: str,
                                     exclude_id: str | None = None) -> int:
        stmt = select(ProviderDeploymentRow.id).where(
            ProviderDeploymentRow.deleted_at.is_(None),
            ProviderDeploymentRow.status == "active",
            ProviderDeploymentRow.model_family == model_alias,
        )
        if exclude_id:
            stmt = stmt.where(ProviderDeploymentRow.id != exclude_id)
        return len((await self.s.execute(stmt)).scalars().all())


class SqlLadderRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, request_class: str, scope: str) -> ModelLadder | None:
        stmt = select(ModelLadderRow).where(
            ModelLadderRow.request_class == request_class,
            ModelLadderRow.scope == scope,
            ModelLadderRow.deleted_at.is_(None),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _LADDER_FIELDS, ModelLadder) if row else None

    async def upsert(self, ladder: ModelLadder) -> ModelLadder:
        row = await self.s.get(ModelLadderRow, ladder.id)
        if ladder.created_at is None:
            ladder.created_at = utcnow()
        ladder.updated_at = utcnow()
        if row is None:
            row = ModelLadderRow()
            _apply(row, ladder, _LADDER_FIELDS)
            self.s.add(row)
        else:
            _apply(row, ladder, _LADDER_FIELDS)
        await self.s.flush()
        return ladder


class SqlBudgetRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, b: Budget) -> None:
        row = BudgetRow()
        _apply(row, b, _BUDGET_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, budget_id: str) -> Budget | None:
        row = await self.s.get(BudgetRow, budget_id)
        if row is None or row.deleted_at is not None:
            return None
        return _budget_entity(row)

    async def update(self, b: Budget) -> None:
        row = await self.s.get(BudgetRow, b.id)
        if row is not None:
            _apply(row, b, _BUDGET_FIELDS)
            await self.s.flush()

    async def list(self, limit: int, cursor: str | None,
                   scope_type: str | None = None) -> Page:
        stmt = select(BudgetRow).where(BudgetRow.deleted_at.is_(None)).order_by(BudgetRow.id)
        if scope_type:
            stmt = stmt.where(BudgetRow.scope_type == scope_type)
        return await _page(self.s, stmt, limit, cursor, _budget_entity)

    async def for_scope(self, scope_type: str, scope_ref: str) -> list[Budget]:
        stmt = select(BudgetRow).where(
            BudgetRow.scope_type == scope_type,
            BudgetRow.scope_ref == scope_ref,
            BudgetRow.deleted_at.is_(None),
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_budget_entity(r) for r in rows]


class SqlKeyRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, k: VirtualKey) -> None:
        row = VirtualKeyRow()
        _apply(row, k, _KEY_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, key_id: str) -> VirtualKey | None:
        row = await self.s.get(VirtualKeyRow, key_id)
        return _to_entity(row, _KEY_FIELDS, VirtualKey) if row else None

    async def get_by_hash_any_tenant(self, key_hash: str) -> VirtualKey | None:
        # Runs under the keyauth GUC policy: SELECT by key_hash crosses tenants
        # because keys authenticate before the tenant is known.
        await self.s.execute(text("SELECT set_config('app.keyauth', 'true', true)"))
        stmt = select(VirtualKeyRow).where(VirtualKeyRow.key_hash == key_hash)
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _KEY_FIELDS, VirtualKey) if row else None

    async def update(self, k: VirtualKey) -> None:
        row = await self.s.get(VirtualKeyRow, k.id)
        if row is not None:
            _apply(row, k, _KEY_FIELDS)
            await self.s.flush()

    async def list(self, limit: int, cursor: str | None) -> Page:
        stmt = select(VirtualKeyRow).where(VirtualKeyRow.deleted_at.is_(None)).order_by(
            VirtualKeyRow.id
        )
        return await _page(self.s, stmt, limit, cursor,
                           lambda r: _to_entity(r, _KEY_FIELDS, VirtualKey))

    async def list_active(self) -> list[VirtualKey]:
        stmt = select(VirtualKeyRow).where(VirtualKeyRow.status == "active")
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _KEY_FIELDS, VirtualKey) for r in rows]


class SqlPolicyRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def current(self) -> GuardrailPolicy | None:
        stmt = (
            select(GuardrailPolicyRow)
            .where(GuardrailPolicyRow.current.is_(True),
                   GuardrailPolicyRow.deleted_at.is_(None))
            .order_by(GuardrailPolicyRow.version.desc())
        )
        row = (await self.s.execute(stmt)).scalars().first()
        if row is None:
            return None
        return GuardrailPolicy(id=row.id, tenant_id=row.tenant_id, policy=row.policy,
                               version=row.version, created_at=row.created_at,
                               updated_at=row.updated_at)

    async def put(self, policy: GuardrailPolicy) -> GuardrailPolicy:
        # history kept via version rows (§4): demote current, insert new
        await self.s.execute(
            update(GuardrailPolicyRow)
            .where(GuardrailPolicyRow.current.is_(True))
            .values(current=False)
        )
        row = GuardrailPolicyRow(
            id=str(uuid7()), tenant_id=self.tenant_id, policy=policy.policy,
            version=policy.version, current=True,
            created_at=policy.created_at or utcnow(),
            updated_at=policy.updated_at or utcnow(),
        )
        self.s.add(row)
        await self.s.flush()
        policy.id = row.id
        return policy


class SqlRequestLogRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, entry: RequestLog) -> None:
        row = RequestLogRow()
        _apply(row, entry, _LOG_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, request_id: str) -> RequestLog | None:
        row = await self.s.get(RequestLogRow, request_id)
        return _to_entity(row, _LOG_FIELDS, RequestLog) if row else None

    async def aggregate_costs(self, since: datetime) -> list[dict]:
        # RLS scopes this to the caller's tenant. Grouped by the dimensions the
        # cost breakdown needs; provider + concrete model id are resolved from
        # deployment_id in the admin layer (deployment rows live in the platform
        # tenant, so the join is done in Python, not across an RLS boundary).
        r = RequestLogRow
        stmt = (
            select(
                r.deployment_id, r.model_alias, r.request_class, r.cached,
                func.count().label("requests"),
                func.coalesce(func.sum(r.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(r.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(r.cost_usd), 0.0).label("cost_usd"),
            )
            .where(r.created_at >= since)
            .group_by(r.deployment_id, r.model_alias, r.request_class, r.cached)
        )
        result = await self.s.execute(stmt)
        return [
            {
                "deployment_id": row.deployment_id, "model_alias": row.model_alias,
                "request_class": row.request_class, "cached": bool(row.cached),
                "requests": int(row.requests), "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": float(row.cost_usd),
            }
            for row in result.all()
        ]


class SqlCacheEntryRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, entry: CacheEntry) -> None:
        row = SemanticCacheEntryRow(
            id=entry.id, tenant_id=entry.tenant_id, prompt_hash=entry.prompt_hash,
            context_hash=entry.context_hash, embedding=entry.embedding,
            response=entry.response, workspace_id=entry.workspace_id,
            expires_at=entry.expires_at, created_at=entry.created_at or utcnow(),
        )
        self.s.add(row)
        await self.s.flush()

    async def search(self, context_hash: str, embedding: list[float],
                     threshold: float, now: datetime) -> CacheEntry | None:
        # pgvector cosine distance: similarity = 1 - distance
        stmt = (
            select(
                SemanticCacheEntryRow,
                SemanticCacheEntryRow.embedding.cosine_distance(embedding).label("dist"),
            )
            .where(
                SemanticCacheEntryRow.context_hash == context_hash,
                SemanticCacheEntryRow.expires_at > now,
            )
            .order_by(text("dist"))
            .limit(1)
        )
        result = (await self.s.execute(stmt)).first()
        if result is None:
            return None
        row, dist = result
        if 1.0 - float(dist) < threshold:
            return None
        return CacheEntry(
            id=row.id, tenant_id=row.tenant_id, prompt_hash=row.prompt_hash,
            context_hash=row.context_hash, embedding=list(row.embedding),
            response=row.response, workspace_id=row.workspace_id,
            expires_at=row.expires_at, created_at=row.created_at,
        )

    async def purge(self, workspace_id: str | None = None) -> int:
        stmt = select(SemanticCacheEntryRow)
        if workspace_id:
            stmt = stmt.where(SemanticCacheEntryRow.workspace_id == workspace_id)
        rows = (await self.s.execute(stmt)).scalars().all()
        for row in rows:
            await self.s.delete(row)
        await self.s.flush()
        return len(rows)


class SqlTenantConfigRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, tenant_id: str) -> TenantConfig | None:
        row = await self.s.get(TenantConfigRow, tenant_id)
        return _to_entity(row, _CFG_FIELDS, TenantConfig) if row else None

    async def put(self, cfg: TenantConfig) -> None:
        row = await self.s.get(TenantConfigRow, cfg.tenant_id)
        if row is None:
            row = TenantConfigRow()
            _apply(row, cfg, _CFG_FIELDS)
            self.s.add(row)
        else:
            _apply(row, cfg, _CFG_FIELDS)
        await self.s.flush()


class SqlOutboxRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def add(self, topic: str, envelope: dict) -> None:
        self.s.add(OutboxRow(id=str(uuid7()), tenant_id=self.tenant_id, topic=topic,
                             payload=envelope, created_at=utcnow()))
        await self.s.flush()


class SqlIdempotencyRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def get(self, key: str) -> dict | None:
        stmt = select(IdempotencyKeyRow).where(IdempotencyKeyRow.key == key)
        row = (await self.s.execute(stmt)).scalars().first()
        if row is None:
            return None
        return {"status_code": row.status_code, "body": row.body}

    async def put(self, key: str, request_hash: str, status_code: int,
                  body: dict) -> None:
        self.s.add(IdempotencyKeyRow(
            id=str(uuid7()), tenant_id=self.tenant_id, key=key,
            request_hash=request_hash, status_code=status_code, body=body,
            created_at=utcnow(),
        ))
        await self.s.flush()


class SqlUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker, tenant_id: str):
        self.tenant_id = tenant_id
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SqlUnitOfWork:
        self._session = self._session_factory()
        # Bind RLS: policies read current_setting('app.tenant_id') (MASTER-FR-001)
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": self.tenant_id},
        )
        s = self._session
        self.providers = SqlProviderRepo(s)
        self.ladders = SqlLadderRepo(s)
        self.budgets = SqlBudgetRepo(s)
        self.keys = SqlKeyRepo(s)
        self.policies = SqlPolicyRepo(s, self.tenant_id)
        self.request_log = SqlRequestLogRepo(s)
        self.cache_entries = SqlCacheEntryRepo(s)
        self.tenant_configs = SqlTenantConfigRepo(s)
        self.outbox = SqlOutboxRepo(s, self.tenant_id)
        self.idempotency = SqlIdempotencyRepo(s, self.tenant_id)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                await self.commit()
            else:
                await self.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": self.tenant_id},
        )

    async def rollback(self) -> None:
        await self._session.rollback()


def sql_uow_factory(session_factory: async_sessionmaker):
    def factory(tenant_id: str) -> SqlUnitOfWork:
        return SqlUnitOfWork(session_factory, tenant_id)

    return factory


class SqlDedupStore:
    """Durable consumer dedup on processed_events (Redis SETNX in prod)."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def seen(self, tenant_id: str, event_id: str) -> bool:
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            stmt = (
                pg_insert(ProcessedEventRow)
                .values(event_id=event_id, tenant_id=tenant_id, created_at=utcnow())
                .on_conflict_do_nothing(index_elements=["event_id"])
                .returning(ProcessedEventRow.event_id)
            )
            inserted = (await session.execute(stmt)).scalar()
            await session.commit()
            return inserted is None


class OutboxDispatcher:
    """Polls unpublished outbox rows and publishes to the bus (MASTER-FR-034).
    Uses the worker policy (`app.worker=true`) to read across tenants."""

    def __init__(self, session_factory: async_sessionmaker, bus, batch_size: int = 100):
        self._session_factory = session_factory
        self._bus = bus
        self._batch = batch_size

    async def run_once(self) -> int:
        async with self._session_factory() as session:
            await session.execute(text("SELECT set_config('app.worker', 'true', true)"))
            stmt = (
                select(OutboxRow)
                .where(OutboxRow.published_at.is_(None))
                .order_by(OutboxRow.created_at.asc())
                .limit(self._batch)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                await self._bus.publish(row.topic, row.payload)
            if rows:
                await session.execute(
                    update(OutboxRow)
                    .where(OutboxRow.id.in_([r.id for r in rows]))
                    .values(published_at=utcnow())
                )
            await session.commit()
            return len(rows)
