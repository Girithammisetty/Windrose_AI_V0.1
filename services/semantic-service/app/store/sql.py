"""SQL repositories + unit of work.

Every tenant UoW opens a transaction and sets `app.tenant_id` so Postgres RLS
(MASTER-FR-001) applies to the non-privileged application role. The outbox
dispatcher uses a worker session (`app.worker=true`, outbox-only policy).
"""

from __future__ import annotations

import dataclasses
import json
import os

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.definition import parse_definition
from app.domain.entities import (
    ChartRef,
    CompileLogEntry,
    ModelVersion,
    Operation,
    SemanticModel,
    VerifiedQuery,
)
from app.domain.ports import Page
from app.store.orm import (
    ChartRefRow,
    CompileLogRow,
    DimensionRow,
    EntityRow,
    IdempotencyKeyRow,
    JoinPathRow,
    MeasureRow,
    ModelVersionRow,
    OperationRow,
    OutboxRow,
    ProcessedEventRow,
    SemanticModelRow,
    VerifiedQueryRow,
)
from app.utils import decode_cursor, encode_cursor, utcnow, uuid7

_MODEL_FIELDS = [f.name for f in dataclasses.fields(SemanticModel)]
_VERSION_FIELDS = [f.name for f in dataclasses.fields(ModelVersion)]
_VQ_FIELDS = [f.name for f in dataclasses.fields(VerifiedQuery)]
_OP_FIELDS = [f.name for f in dataclasses.fields(Operation)]


def make_engine(database_url: str):
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )


def _to_entity(row, fields, cls, **overrides):
    values = {f: overrides.get(f, getattr(row, f, None)) for f in fields}
    return cls(**values)


def _apply(row, entity, fields, skip=()):
    for f in fields:
        if f not in skip:
            setattr(row, f, getattr(entity, f))


def _vector_literal(embedding: list[float] | None) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


def _vector_parse(literal: str | None) -> list[float] | None:
    if not literal:
        return None
    return [float(x) for x in literal.strip("[]").split(",") if x]


class SqlModelRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, model: SemanticModel) -> None:
        row = SemanticModelRow()
        _apply(row, model, _MODEL_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, model_id: str, include_deleted: bool = False) -> SemanticModel | None:
        row = await self.s.get(SemanticModelRow, model_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            return None
        return _to_entity(row, _MODEL_FIELDS, SemanticModel)

    async def get_by_name(self, workspace_id: str, name: str) -> SemanticModel | None:
        stmt = select(SemanticModelRow).where(
            SemanticModelRow.workspace_id == workspace_id,
            func.lower(SemanticModelRow.name) == name.lower(),
            SemanticModelRow.deleted_at.is_(None),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _MODEL_FIELDS, SemanticModel) if row else None

    async def update(self, model: SemanticModel) -> None:
        row = await self.s.get(SemanticModelRow, model.id)
        if row is not None:
            _apply(row, model, _MODEL_FIELDS)
            await self.s.flush()

    async def list(self, workspace_id: str | None, limit: int,
                   cursor: str | None) -> Page:
        stmt = select(SemanticModelRow).where(SemanticModelRow.deleted_at.is_(None))
        if workspace_id:
            stmt = stmt.where(SemanticModelRow.workspace_id == workspace_id)
        stmt = stmt.order_by(SemanticModelRow.created_at.desc(),
                             SemanticModelRow.id.desc())
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        return Page(
            items=[_to_entity(r, _MODEL_FIELDS, SemanticModel) for r in rows[:limit]],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def all_active(self) -> list[SemanticModel]:
        rows = (await self.s.execute(
            select(SemanticModelRow).where(SemanticModelRow.deleted_at.is_(None))
        )).scalars().all()
        return [_to_entity(r, _MODEL_FIELDS, SemanticModel) for r in rows]


class SqlVersionRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def add(self, version: ModelVersion) -> None:
        row = ModelVersionRow()
        _apply(row, version, _VERSION_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, model_id: str, version_no: int) -> ModelVersion | None:
        stmt = select(ModelVersionRow).where(
            ModelVersionRow.model_id == model_id,
            ModelVersionRow.version_no == version_no,
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, ModelVersion) if row else None

    async def get_by_id(self, version_id: str) -> ModelVersion | None:
        row = await self.s.get(ModelVersionRow, version_id)
        return _to_entity(row, _VERSION_FIELDS, ModelVersion) if row else None

    async def latest(self, model_id: str) -> ModelVersion | None:
        stmt = (select(ModelVersionRow)
                .where(ModelVersionRow.model_id == model_id)
                .order_by(ModelVersionRow.version_no.desc()).limit(1))
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, ModelVersion) if row else None

    async def open_version(self, model_id: str) -> ModelVersion | None:
        stmt = select(ModelVersionRow).where(
            ModelVersionRow.model_id == model_id,
            ModelVersionRow.status.in_(["draft", "in_review", "rejected"]),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, ModelVersion) if row else None

    async def list(self, model_id: str, limit: int, cursor: str | None) -> Page:
        stmt = (select(ModelVersionRow)
                .where(ModelVersionRow.model_id == model_id)
                .order_by(ModelVersionRow.version_no.desc()))
        if cursor:
            stmt = stmt.where(ModelVersionRow.version_no < int(decode_cursor(cursor)["v"]))
        rows = (await self.s.execute(stmt.limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        items = rows[:limit]
        return Page(
            items=[_to_entity(r, _VERSION_FIELDS, ModelVersion) for r in items],
            next_cursor=(encode_cursor({"v": items[-1].version_no})
                         if has_more and items else None),
            has_more=has_more,
        )

    async def update(self, version: ModelVersion) -> None:
        row = await self.s.get(ModelVersionRow, version.id)
        if row is not None:
            _apply(row, version, _VERSION_FIELDS)
            await self.s.flush()

    async def lock_model(self, model_id: str) -> None:
        # BR-10: per-model advisory lock serializes concurrent publications
        await self.s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:mid, 6))"),
            {"mid": model_id},
        )

    async def rebuild_projections(self, version: ModelVersion) -> None:
        """Rebuild normalized entity/dimension/measure/join rows (BRD §4.1)."""
        for model in (EntityRow, DimensionRow, MeasureRow, JoinPathRow):
            rows = (await self.s.execute(
                select(model).where(model.model_version_id == version.id)
            )).scalars().all()
            for r in rows:
                await self.s.delete(r)
        defn = parse_definition(version.definition)
        for e in defn.entities.values():
            self.s.add(EntityRow(
                id=str(uuid7()), tenant_id=self.tenant_id, model_version_id=version.id,
                name=e.name, dataset_urn=e.dataset_urn, physical_table=e.table,
                version_policy=e.dataset_version_policy, primary_key=e.primary_key))
        for d in defn.dimensions.values():
            self.s.add(DimensionRow(
                id=str(uuid7()), tenant_id=self.tenant_id, model_version_id=version.id,
                entity_name=d.entity, name=d.name, column=d.column, expr_ast=d.expr_ast,
                dim_type=d.dim_type, time_grains=d.time_grains, synonyms=d.synonyms,
                deprecated=d.deprecated, successor=d.successor))
        for m in defn.measures.values():
            self.s.add(MeasureRow(
                id=str(uuid7()), tenant_id=self.tenant_id, model_version_id=version.id,
                entity_name=m.entity, name=m.name, agg=m.agg,
                expr_ast=m.expr_ast or m.expr_metric_ast, filters_ast=m.filters_ast,
                synonyms=m.synonyms, deprecated=m.deprecated, successor=m.successor))
        for j in defn.join_paths.values():
            self.s.add(JoinPathRow(
                id=str(uuid7()), tenant_id=self.tenant_id, model_version_id=version.id,
                name=j.name, from_entity=j.from_entity, to_entity=j.to_entity,
                join_type=j.join_type, on_pairs=j.on, cardinality=j.cardinality))
        await self.s.flush()


class SqlVerifiedQueryRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    def _entity(self, row: VerifiedQueryRow) -> VerifiedQuery:
        return _to_entity(row, _VQ_FIELDS, VerifiedQuery,
                          embedding=_vector_parse(row.embedding))

    async def add(self, vq: VerifiedQuery) -> None:
        row = VerifiedQueryRow()
        _apply(row, vq, _VQ_FIELDS, skip=("embedding",))
        row.embedding = _vector_literal(vq.embedding)
        self.s.add(row)
        await self.s.flush()

    async def get(self, vq_id: str) -> VerifiedQuery | None:
        row = await self.s.get(VerifiedQueryRow, vq_id)
        if row is None or row.deleted_at is not None:
            return None
        return self._entity(row)

    async def update(self, vq: VerifiedQuery) -> None:
        row = await self.s.get(VerifiedQueryRow, vq.id)
        if row is not None:
            _apply(row, vq, _VQ_FIELDS, skip=("embedding",))
            row.embedding = _vector_literal(vq.embedding)
            await self.s.flush()

    async def list(self, workspace_id: str | None, status: str | None,
                   limit: int, cursor: str | None) -> Page:
        stmt = select(VerifiedQueryRow).where(VerifiedQueryRow.deleted_at.is_(None))
        if workspace_id:
            stmt = stmt.where(VerifiedQueryRow.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(VerifiedQueryRow.status == status)
        stmt = stmt.order_by(VerifiedQueryRow.created_at.desc(), VerifiedQueryRow.id.desc())
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        return Page(
            items=[self._entity(r) for r in rows[:limit]],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def search(self, workspace_id: str, embedding: list[float],
                     top_k: int) -> list[tuple[VerifiedQuery, float]]:
        # BR-14: tenant (RLS) + workspace + approved are hard SQL predicates.
        # Cosine distance via pgvector `<=>`; similarity = 1 - distance.
        stmt = text(
            "SELECT id, 1 - (embedding <=> CAST(:emb AS vector)) AS score "
            "FROM verified_queries "
            "WHERE workspace_id = :ws AND status = 'approved' "
            "AND deleted_at IS NULL AND embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:emb AS vector) ASC, id ASC LIMIT :k"
        )
        result = await self.s.execute(
            stmt, {"emb": _vector_literal(embedding), "ws": workspace_id, "k": top_k})
        out: list[tuple[VerifiedQuery, float]] = []
        for vq_id, score in result.all():
            row = await self.s.get(VerifiedQueryRow, str(vq_id))
            if row is not None:
                out.append((self._entity(row), float(score)))
        return out

    async def approved_for_model(self, model_id: str) -> list[VerifiedQuery]:
        stmt = select(VerifiedQueryRow).where(
            VerifiedQueryRow.model_id == model_id,
            VerifiedQueryRow.status == "approved",
            VerifiedQueryRow.deleted_at.is_(None),
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [self._entity(r) for r in rows]

    async def approved_all(self) -> list[VerifiedQuery]:
        stmt = select(VerifiedQueryRow).where(
            VerifiedQueryRow.status == "approved",
            VerifiedQueryRow.deleted_at.is_(None),
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [self._entity(r) for r in rows]


class SqlCompileLogRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, entry: CompileLogEntry) -> None:
        row = CompileLogRow()
        _apply(row, entry, [f.name for f in dataclasses.fields(CompileLogEntry)])
        self.s.add(row)
        await self.s.flush()


class SqlOperationRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, op: Operation) -> None:
        row = OperationRow()
        _apply(row, op, _OP_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, op_id: str) -> Operation | None:
        row = await self.s.get(OperationRow, op_id)
        return _to_entity(row, _OP_FIELDS, Operation) if row else None

    async def update(self, op: Operation) -> None:
        row = await self.s.get(OperationRow, op.id)
        if row is not None:
            _apply(row, op, _OP_FIELDS)
            await self.s.flush()


class SqlChartRefRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def upsert(self, ref: ChartRef) -> None:
        stmt = pg_insert(ChartRefRow).values(
            tenant_id=ref.tenant_id, chart_urn=ref.chart_urn,
            model=ref.model, measures=ref.measures,
        ).on_conflict_do_update(
            index_elements=["tenant_id", "chart_urn"],
            set_={"model": ref.model, "measures": ref.measures},
        )
        await self.s.execute(stmt)

    async def charts_referencing(self, measure: str) -> list[ChartRef]:
        stmt = select(ChartRefRow).where(ChartRefRow.measures.contains([measure]))
        rows = (await self.s.execute(stmt)).scalars().all()
        return [ChartRef(tenant_id=r.tenant_id, chart_urn=r.chart_urn,
                         model=r.model, measures=list(r.measures)) for r in rows]


class SqlOutboxRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def add(self, topic: str, envelope: dict) -> None:
        self.s.add(OutboxRow(
            id=str(uuid7()), tenant_id=self.tenant_id, topic=topic,
            event_type=envelope["event_type"],
            payload=json.loads(json.dumps(envelope, default=str)),
            created_at=utcnow(),
        ))
        await self.s.flush()


class SqlIdempotencyRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def get(self, key: str) -> dict | None:
        row = await self.s.get(IdempotencyKeyRow, (self.tenant_id, key))
        if row is None:
            return None
        return {"request_hash": row.request_hash, "status_code": row.status_code,
                "body": row.response_body}

    async def put(self, key: str, request_hash: str, status_code: int, body: dict) -> None:
        self.s.add(IdempotencyKeyRow(
            tenant_id=self.tenant_id, key=key, request_hash=request_hash,
            status_code=status_code, response_body=body, created_at=utcnow(),
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
        self.models = SqlModelRepo(self._session)
        self.versions = SqlVersionRepo(self._session, self.tenant_id)
        self.verified_queries = SqlVerifiedQueryRepo(self._session, self.tenant_id)
        self.compile_log = SqlCompileLogRepo(self._session)
        self.operations = SqlOperationRepo(self._session)
        self.chart_refs = SqlChartRefRepo(self._session, self.tenant_id)
        self.outbox = SqlOutboxRepo(self._session, self.tenant_id)
        self.idempotency = SqlIdempotencyRepo(self._session, self.tenant_id)
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
        # Re-arm tenant GUC for any follow-up statements in this UoW's lifetime.
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
