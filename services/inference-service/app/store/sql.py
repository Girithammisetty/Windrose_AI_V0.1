"""SQL repositories + unit of work (RLS-bound, MASTER-FR-001).

Every tenant UoW opens a transaction and sets ``app.tenant_id`` so Postgres RLS
applies to the non-privileged application role. Worker sessions (scheduler,
outbox relay, reaper) set ``app.worker=true`` to read across tenants.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.entities import InferenceJob, LineageEdge, ScoringSchedule
from app.domain.enums import JobStatus
from app.domain.ports import Filters, Page, ResolvedDataset
from app.store.orm import (
    IdempotencyKeyRow,
    InferenceJobRow,
    InputDatasetRow,
    JobQueueRow,
    LineageEdgeRow,
    OutboxRow,
    OutputDatasetRow,
    OutputDatasetVersionRow,
    ProcessedEventRow,
    ScoringScheduleRow,
)
from app.utils import decode_cursor, encode_cursor, utcnow, uuid7

_JOB_FIELDS = [
    "id", "tenant_id", "workspace_id", "name", "description", "status",
    "model_version_urn", "model_name", "model_version", "model_stage_at_submit",
    "input_dataset_urn", "input_dataset_version", "output_dataset_urn",
    "output_dataset_version", "output_mode", "output_dataset_name", "parameters",
    "compatibility_report", "pipeline_run_urn", "components_status", "error",
    "row_count", "schedule_id", "retried_from_job_id", "submitted_by", "via_agent",
    "queued_at", "submitted_at", "started_at", "finished_at", "created_at",
    "updated_at", "deleted_at",
]
_SCHEDULE_FIELDS = [
    "id", "tenant_id", "workspace_id", "name", "model_version_urn", "model_urn",
    "stage_selector", "input_selector", "cron", "interval_seconds", "timezone",
    "overlap_policy", "output", "enabled", "paused_reason", "consecutive_failures",
    "temporal_schedule_id", "notify_on_failure", "last_fired_at", "next_fire_at",
    "created_by", "created_at", "updated_at", "deleted_at",
]
_EDGE_FIELDS = [
    "id", "tenant_id", "from_urn", "to_urn", "activity", "run_urn", "properties",
    "occurred_at", "created_at",
]


def make_engine(database_url: str):
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )


def _to_entity(row, fields, cls):
    return cls(**{f: getattr(row, f) for f in fields})


def _apply(row, entity, fields):
    for f in fields:
        setattr(row, f, getattr(entity, f))


class SqlJobRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, job: InferenceJob) -> None:
        row = InferenceJobRow()
        _apply(row, job, _JOB_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def _row(self, job_id: str) -> InferenceJobRow | None:
        return await self.s.get(InferenceJobRow, job_id)

    async def get(self, job_id: str, include_deleted: bool = False) -> InferenceJob | None:
        row = await self._row(job_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            return None
        return _to_entity(row, _JOB_FIELDS, InferenceJob)

    async def update(self, job: InferenceJob) -> None:
        row = await self._row(job.id)
        if row is not None:
            _apply(row, job, _JOB_FIELDS)
            await self.s.flush()

    async def get_by_name(self, workspace_id: str, name: str) -> InferenceJob | None:
        stmt = select(InferenceJobRow).where(
            InferenceJobRow.workspace_id == workspace_id,
            InferenceJobRow.name == name,
            InferenceJobRow.deleted_at.is_(None),
            InferenceJobRow.schedule_id.is_(None),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _JOB_FIELDS, InferenceJob) if row else None

    async def by_pipeline_run_urn(self, run_urn: str) -> InferenceJob | None:
        stmt = select(InferenceJobRow).where(InferenceJobRow.pipeline_run_urn == run_urn)
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _JOB_FIELDS, InferenceJob) if row else None

    async def count_active(self, workspace_id: str | None = None) -> int:
        active = [JobStatus.submitted, JobStatus.running, JobStatus.finalizing,
                  JobStatus.cancelling]
        stmt = select(func.count()).select_from(InferenceJobRow).where(
            InferenceJobRow.status.in_(active)
        )
        result = await self.s.execute(stmt)
        return int(result.scalar_one())

    async def list(self, filters: Filters, sort: str, limit: int,
                   cursor: str | None) -> Page:
        stmt = select(InferenceJobRow).where(InferenceJobRow.deleted_at.is_(None))
        if filters.status is not None:
            stmt = stmt.where(InferenceJobRow.status == filters.status)
        if filters.model_version_urn:
            stmt = stmt.where(InferenceJobRow.model_version_urn == filters.model_version_urn)
        if filters.schedule_id:
            stmt = stmt.where(InferenceJobRow.schedule_id == filters.schedule_id)
        descending = not sort.startswith("created_at")
        order = InferenceJobRow.created_at
        if cursor:
            after = decode_cursor(cursor)
            val = datetime.fromisoformat(after["v"])
            last_id = after["id"]
            if descending:
                stmt = stmt.where((order < val) | ((order == val) & (InferenceJobRow.id < last_id)))
            else:
                stmt = stmt.where((order > val) | ((order == val) & (InferenceJobRow.id > last_id)))
        stmt = stmt.order_by(
            order.desc() if descending else order.asc(),
            InferenceJobRow.id.desc() if descending else InferenceJobRow.id.asc(),
        ).limit(limit + 1)
        rows = (await self.s.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor({"v": last.created_at.isoformat(), "id": last.id})
        return Page(
            items=[_to_entity(r, _JOB_FIELDS, InferenceJob) for r in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def running_started_before(self, cutoff: datetime) -> list[InferenceJob]:
        """Non-terminal jobs that have actually started running (submitted/running/
        finalizing/cancelling) before ``cutoff`` — excludes ``queued`` which has its
        own shorter timeout (see ``queued_before``)."""
        active = [JobStatus.submitted, JobStatus.running, JobStatus.finalizing,
                  JobStatus.cancelling]
        stmt = select(InferenceJobRow).where(
            InferenceJobRow.status.in_(active),
            func.coalesce(
                InferenceJobRow.submitted_at, InferenceJobRow.created_at
            ) < cutoff,
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _JOB_FIELDS, InferenceJob) for r in rows]

    async def queued_before(self, cutoff: datetime) -> list[InferenceJob]:
        """Jobs still ``queued`` past the (short) queued timeout (BR-12)."""
        stmt = select(InferenceJobRow).where(
            InferenceJobRow.status == int(JobStatus.queued),
            func.coalesce(
                InferenceJobRow.queued_at, InferenceJobRow.created_at
            ) < cutoff,
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _JOB_FIELDS, InferenceJob) for r in rows]

    async def last_for_schedule(self, schedule_id: str) -> InferenceJob | None:
        stmt = (
            select(InferenceJobRow)
            .where(InferenceJobRow.schedule_id == schedule_id)
            .order_by(InferenceJobRow.created_at.desc())
            .limit(1)
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _JOB_FIELDS, InferenceJob) if row else None


class SqlScheduleRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, sch: ScoringSchedule) -> None:
        row = ScoringScheduleRow()
        _apply(row, sch, _SCHEDULE_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, schedule_id: str, include_deleted: bool = False) -> ScoringSchedule | None:
        row = await self.s.get(ScoringScheduleRow, schedule_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            return None
        return _to_entity(row, _SCHEDULE_FIELDS, ScoringSchedule)

    async def update(self, sch: ScoringSchedule) -> None:
        row = await self.s.get(ScoringScheduleRow, sch.id)
        if row is not None:
            _apply(row, sch, _SCHEDULE_FIELDS)
            await self.s.flush()

    async def get_by_name(self, workspace_id: str, name: str) -> ScoringSchedule | None:
        stmt = select(ScoringScheduleRow).where(
            ScoringScheduleRow.workspace_id == workspace_id,
            ScoringScheduleRow.name == name,
            ScoringScheduleRow.deleted_at.is_(None),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _SCHEDULE_FIELDS, ScoringSchedule) if row else None

    async def count_enabled(self) -> int:
        stmt = select(func.count()).select_from(ScoringScheduleRow).where(
            ScoringScheduleRow.enabled.is_(True), ScoringScheduleRow.deleted_at.is_(None)
        )
        return int((await self.s.execute(stmt)).scalar_one())

    async def list(self, limit: int, cursor: str | None) -> Page:
        stmt = (
            select(ScoringScheduleRow)
            .where(ScoringScheduleRow.deleted_at.is_(None))
            .order_by(ScoringScheduleRow.created_at.desc(), ScoringScheduleRow.id.desc())
        )
        if cursor:
            after = decode_cursor(cursor)
            val = datetime.fromisoformat(after["v"])
            stmt = stmt.where(
                (ScoringScheduleRow.created_at < val)
                | ((ScoringScheduleRow.created_at == val) & (ScoringScheduleRow.id < after["id"]))
            )
        rows = (await self.s.execute(stmt.limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more and items:
            next_cursor = encode_cursor({"v": items[-1].created_at.isoformat(), "id": items[-1].id})
        return Page(
            items=[_to_entity(r, _SCHEDULE_FIELDS, ScoringSchedule) for r in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def all_enabled(self) -> list[ScoringSchedule]:
        """Worker-session read across tenants (scheduler tick)."""
        stmt = select(ScoringScheduleRow).where(
            ScoringScheduleRow.enabled.is_(True), ScoringScheduleRow.deleted_at.is_(None)
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _SCHEDULE_FIELDS, ScoringSchedule) for r in rows]


class SqlQueueRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def enqueue(self, job_id: str) -> None:
        self.s.add(
            JobQueueRow(
                id=str(uuid7()), tenant_id=self.tenant_id, job_id=job_id,
                enqueued_at=utcnow(),
            )
        )
        await self.s.flush()

    async def depth(self) -> int:
        stmt = select(func.count()).select_from(JobQueueRow)
        return int((await self.s.execute(stmt)).scalar_one())

    async def next_job_id(self) -> str | None:
        stmt = (
            select(JobQueueRow)
            .order_by(JobQueueRow.enqueued_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return row.job_id if row else None

    async def remove(self, job_id: str) -> None:
        row = (
            await self.s.execute(select(JobQueueRow).where(JobQueueRow.job_id == job_id))
        ).scalars().first()
        if row is not None:
            await self.s.delete(row)
            await self.s.flush()


class SqlInputRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, urn: str, version: int | None = None) -> ResolvedDataset | None:
        stmt = select(InputDatasetRow).where(InputDatasetRow.urn == urn)
        if version is not None:
            stmt = stmt.where(InputDatasetRow.version_no == version)
        stmt = stmt.order_by(InputDatasetRow.version_no.desc()).limit(1)
        row = (await self.s.execute(stmt)).scalars().first()
        if row is None:
            return None
        return ResolvedDataset(
            urn=row.urn, dataset_id=row.dataset_id, version=row.version_no,
            schema=row.schema, row_count=row.row_count, storage_uri=row.storage_uri,
        )

    async def upsert(self, *, urn: str, dataset_id: str, version_no: int, schema: dict,
                     storage_uri: str, row_count: int, tenant_id: str) -> None:
        self.s.add(
            InputDatasetRow(
                id=str(uuid7()), tenant_id=tenant_id, urn=urn, dataset_id=dataset_id,
                version_no=version_no, schema=schema, storage_uri=storage_uri,
                row_count=row_count, created_at=utcnow(),
            )
        )
        await self.s.flush()


class SqlOutputRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def find(self, workspace_id: str, name: str) -> OutputDatasetRow | None:
        stmt = select(OutputDatasetRow).where(
            OutputDatasetRow.workspace_id == workspace_id, OutputDatasetRow.name == name
        )
        return (await self.s.execute(stmt)).scalars().first()

    async def version_for_job(self, job_id: str) -> OutputDatasetVersionRow | None:
        stmt = select(OutputDatasetVersionRow).where(
            OutputDatasetVersionRow.produced_by_job_id == job_id
        )
        return (await self.s.execute(stmt)).scalars().first()

    async def create_dataset(self, *, workspace_id: str, name: str, urn: str,
                             owner_model_urn: str) -> OutputDatasetRow:
        row = OutputDatasetRow(
            id=str(uuid7()), tenant_id=self.tenant_id, workspace_id=workspace_id,
            name=name, urn=urn, owner_model_urn=owner_model_urn, current_version=0,
            created_at=utcnow(), updated_at=utcnow(),
        )
        self.s.add(row)
        await self.s.flush()
        return row

    async def add_version(self, *, dataset_id: str, version_no: int, storage_uri: str,
                          snapshot_id: str, row_count: int, job_id: str) -> None:
        self.s.add(
            OutputDatasetVersionRow(
                id=str(uuid7()), tenant_id=self.tenant_id, dataset_id=dataset_id,
                version_no=version_no, storage_uri=storage_uri, snapshot_id=snapshot_id,
                row_count=row_count, produced_by_job_id=job_id, created_at=utcnow(),
            )
        )
        await self.s.flush()

    async def bump_version(self, row: OutputDatasetRow, version_no: int) -> None:
        row.current_version = version_no
        row.updated_at = utcnow()
        await self.s.flush()


class SqlLineageRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def upsert(self, edge: LineageEdge) -> bool:
        values = {f: getattr(edge, f) for f in _EDGE_FIELDS}
        stmt = (
            pg_insert(LineageEdgeRow)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "from_urn", "to_urn", "activity", "run_urn"]
            )
            .returning(LineageEdgeRow.id)
        )
        return (await self.s.execute(stmt)).scalar() is not None

    async def edges_touching(self, urn: str, direction: str) -> list[LineageEdge]:
        conds = []
        if direction in ("downstream", "both"):
            conds.append(LineageEdgeRow.from_urn == urn)
        if direction in ("upstream", "both"):
            conds.append(LineageEdgeRow.to_urn == urn)
        cond = conds[0] if len(conds) == 1 else conds[0] | conds[1]
        rows = (await self.s.execute(select(LineageEdgeRow).where(cond))).scalars().all()
        return [_to_entity(r, _EDGE_FIELDS, LineageEdge) for r in rows]


class SqlOutboxRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def add(self, topic: str, envelope: dict) -> None:
        self.s.add(
            OutboxRow(
                id=str(uuid7()), tenant_id=self.tenant_id, topic=topic,
                event_type=envelope["event_type"], payload=envelope, created_at=utcnow(),
            )
        )
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
        self.s.add(
            IdempotencyKeyRow(
                tenant_id=self.tenant_id, key=key, request_hash=request_hash,
                status_code=status_code, response_body=body, created_at=utcnow(),
            )
        )
        await self.s.flush()


class SqlUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker, tenant_id: str,
                 *, worker: bool = False):
        self.tenant_id = tenant_id
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._worker = worker

    async def __aenter__(self) -> SqlUnitOfWork:
        self._session = self._session_factory()
        # Worker sessions read across tenants via the app.worker policy; they must
        # NOT set a bogus tenant GUC (e.g. "*") — the RLS tenant policy casts
        # app.tenant_id to uuid and would error. Set it empty (-> NULL, no match)
        # and rely on the worker policy.
        tid = "" if self._worker else self.tenant_id
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid}
        )
        if self._worker:
            await self._session.execute(text("SELECT set_config('app.worker', 'true', true)"))
        self.jobs = SqlJobRepo(self._session)
        self.schedules = SqlScheduleRepo(self._session)
        self.queue = SqlQueueRepo(self._session, self.tenant_id)
        self.inputs = SqlInputRepo(self._session)
        self.outputs = SqlOutputRepo(self._session, self.tenant_id)
        self.lineage = SqlLineageRepo(self._session, self.tenant_id)
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
        tid = "" if self._worker else self.tenant_id
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid}
        )
        if self._worker:
            await self._session.execute(text("SELECT set_config('app.worker', 'true', true)"))

    async def rollback(self) -> None:
        await self._session.rollback()


def sql_uow_factory(session_factory: async_sessionmaker):
    def factory(tenant_id: str, *, worker: bool = False) -> SqlUnitOfWork:
        return SqlUnitOfWork(session_factory, tenant_id, worker=worker)

    return factory


class SqlDedupStore:
    """Durable consumer dedup on processed_events (Redis in prod)."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            return await session.get(ProcessedEventRow, event_id) is not None

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            stmt = (
                pg_insert(ProcessedEventRow)
                .values(event_id=event_id, tenant_id=tenant_id, created_at=utcnow())
                .on_conflict_do_nothing(index_elements=["event_id"])
            )
            await session.execute(stmt)
            await session.commit()

    async def claim(self, tenant_id: str, event_id: str) -> bool:
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
            won = (await session.execute(stmt)).scalar() is not None
            await session.commit()
            return won


class OutboxDispatcher:
    """Polls unpublished outbox rows and publishes to the bus (MASTER-FR-034).
    Uses the worker policy (``app.worker=true``) to read across tenants."""

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
