"""SQL repositories + unit of work.

Every tenant UoW opens a transaction and sets `app.tenant_id` so Postgres RLS
(MASTER-FR-001) applies to the non-privileged application role. The outbox
dispatcher uses a worker session (`app.worker=true`, outbox-only policy).
"""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.entities import (
    Dataset,
    DatasetVersion,
    EntityMergeCandidate,
    EntityResolutionConfig,
    EntityResolutionRun,
    LineageEdge,
    Profile,
    ResolvedEntity,
    ResolvedEntityMember,
)
from app.domain.ports import DatasetFilters, Page
from app.store.orm import (
    DatasetRow,
    DatasetVersionRow,
    IdempotencyKeyRow,
    LineageEdgeRow,
    MergeCandidateRow,
    OutboxRow,
    ProcessedEventRow,
    ProfileRow,
    ResolutionConfigRow,
    ResolutionRunRow,
    ResolvedEntityMemberRow,
    ResolvedEntityRow,
)
from app.utils import decode_cursor, encode_cursor, utcnow, uuid7

_DATASET_FIELDS = [f.name for f in dataclasses.fields(Dataset)]
_VERSION_FIELDS = [f.name for f in dataclasses.fields(DatasetVersion)]
_PROFILE_FIELDS = [f.name for f in dataclasses.fields(Profile)]
_EDGE_FIELDS = [f.name for f in dataclasses.fields(LineageEdge)]


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


class SqlDatasetRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, dataset: Dataset) -> None:
        row = DatasetRow()
        _apply(row, dataset, _DATASET_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def _row(self, dataset_id: str) -> DatasetRow | None:
        return await self.s.get(DatasetRow, dataset_id)

    async def get(self, dataset_id: str, include_deleted: bool = False) -> Dataset | None:
        row = await self._row(dataset_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            return None
        return _to_entity(row, _DATASET_FIELDS, Dataset)

    async def get_by_name(self, workspace_id: str, name: str) -> Dataset | None:
        stmt = select(DatasetRow).where(
            DatasetRow.workspace_id == workspace_id,
            func.lower(DatasetRow.name) == name.lower(),
            DatasetRow.deleted_at.is_(None),
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _DATASET_FIELDS, Dataset) if row else None

    async def get_by_name_in_tenant(self, name: str) -> Dataset | None:
        # No workspace filter: RLS (app.tenant_id GUC) already scopes to the
        # tenant, so this returns the tenant's dataset with this name regardless
        # of workspace (the /resolve caller only knows tenant + name).
        stmt = select(DatasetRow).where(
            func.lower(DatasetRow.name) == name.lower(),
            DatasetRow.deleted_at.is_(None),
        ).order_by(DatasetRow.created_at.desc())
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _DATASET_FIELDS, Dataset) if row else None

    async def update(self, dataset: Dataset) -> None:
        row = await self._row(dataset.id)
        if row is not None:
            _apply(row, dataset, _DATASET_FIELDS)
            await self.s.flush()

    async def list(self, filters: DatasetFilters, sort: str, limit: int,
                   cursor: str | None) -> Page:
        stmt = select(DatasetRow)
        if not filters.include_deleted:
            stmt = stmt.where(DatasetRow.deleted_at.is_(None))
        if filters.status:
            stmt = stmt.where(DatasetRow.status == filters.status)
        if filters.created_by:
            stmt = stmt.where(DatasetRow.created_by == filters.created_by)
        if filters.tags:
            stmt = stmt.where(DatasetRow.tags.contains(filters.tags))
        if filters.ids is not None:
            stmt = stmt.where(DatasetRow.id.in_(filters.ids))

        needs_version = filters.column or filters.has_pii is not None or filters.quality_flag
        if needs_version:
            stmt = stmt.join(
                DatasetVersionRow, DatasetVersionRow.id == DatasetRow.current_version_id
            )
            if filters.column:
                stmt = stmt.where(
                    text(
                        "EXISTS (SELECT 1 FROM jsonb_object_keys(dataset_versions.schema) k "
                        "WHERE lower(k) = lower(:col))"
                    ).bindparams(col=filters.column)
                )
            if filters.has_pii is not None:
                pii_expr = text(
                    "(EXISTS (SELECT 1 FROM unnest(datasets.tags) t WHERE t LIKE 'pii%') "
                    "OR dataset_versions.schema::text LIKE '%pii%')"
                )
                stmt = stmt.where(pii_expr if filters.has_pii else ~pii_expr)
            if filters.quality_flag:
                stmt = stmt.join(
                    ProfileRow, ProfileRow.id == DatasetVersionRow.profile_id, isouter=True
                ).where(
                    text(
                        "EXISTS (SELECT 1 FROM jsonb_array_elements("
                        "coalesce(profiles.summary->'columns','[]'::jsonb)) c "
                        "WHERE c->'quality_flags' ? :flag)"
                    ).bindparams(flag=filters.quality_flag)
                )

        if filters.ids is not None:
            # Preserve search-index ranking; offset cursor over the ranked set.
            rows = (await self.s.execute(stmt)).scalars().all()
            rank = {did: i for i, did in enumerate(filters.ids)}
            rows = sorted(rows, key=lambda r: rank.get(r.id, 1_000_000))
            offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
            window = rows[offset : offset + limit]
            has_more = offset + limit < len(rows)
            return Page(
                items=[_to_entity(r, _DATASET_FIELDS, Dataset) for r in window],
                next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
                has_more=has_more,
            )

        descending = sort.startswith("-")
        key = sort.lstrip("-")
        if key == "name":
            order_col, cursor_val = func.lower(DatasetRow.name), lambda r: r.name.lower()
        elif key == "row_count":
            stmt = stmt.join(
                DatasetVersionRow,
                DatasetVersionRow.id == DatasetRow.current_version_id,
                isouter=True,
            ) if not needs_version else stmt
            order_col = func.coalesce(DatasetVersionRow.row_count, 0)
            cursor_val = None  # offset cursor for computed sort
        else:
            order_col, cursor_val = DatasetRow.created_at, lambda r: r.created_at.isoformat()

        if cursor_val is None:
            stmt = stmt.order_by(order_col.desc() if descending else order_col.asc(),
                                 DatasetRow.id.desc())
            offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
            rows = (await self.s.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
            has_more = len(rows) > limit
            return Page(
                items=[_to_entity(r, _DATASET_FIELDS, Dataset) for r in rows[:limit]],
                next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
                has_more=has_more,
            )

        if cursor:
            after = decode_cursor(cursor)
            val, last_id = after["v"], after["id"]
            if key == "created_at":
                val = datetime.fromisoformat(val)
            if descending:
                stmt = stmt.where(
                    (order_col < val) | ((order_col == val) & (DatasetRow.id < last_id))
                )
            else:
                stmt = stmt.where(
                    (order_col > val) | ((order_col == val) & (DatasetRow.id > last_id))
                )
        stmt = stmt.order_by(
            order_col.desc() if descending else order_col.asc(),
            DatasetRow.id.desc() if descending else DatasetRow.id.asc(),
        ).limit(limit + 1)
        rows = (await self.s.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor({"v": cursor_val(last), "id": last.id})
        return Page(
            items=[_to_entity(r, _DATASET_FIELDS, Dataset) for r in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def all_active(self) -> list[Dataset]:
        rows = (
            await self.s.execute(select(DatasetRow).where(DatasetRow.deleted_at.is_(None)))
        ).scalars().all()
        return [_to_entity(r, _DATASET_FIELDS, Dataset) for r in rows]

    async def soft_deleted_before(self, cutoff: datetime) -> list[Dataset]:
        rows = (
            await self.s.execute(select(DatasetRow).where(DatasetRow.deleted_at < cutoff))
        ).scalars().all()
        return [_to_entity(r, _DATASET_FIELDS, Dataset) for r in rows]

    async def hard_delete(self, dataset_id: str) -> None:
        for model in (ProfileRow, DatasetVersionRow):
            rows = (
                await self.s.execute(select(model).where(model.dataset_id == dataset_id))
            ).scalars().all()
            for r in rows:
                await self.s.delete(r)
        row = await self._row(dataset_id)
        if row is not None:
            await self.s.delete(row)
        await self.s.flush()


class SqlVersionRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, version: DatasetVersion) -> None:
        row = DatasetVersionRow()
        _apply(row, version, _VERSION_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, dataset_id: str, version_no: int) -> DatasetVersion | None:
        stmt = select(DatasetVersionRow).where(
            DatasetVersionRow.dataset_id == dataset_id,
            DatasetVersionRow.version_no == version_no,
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, DatasetVersion) if row else None

    async def get_by_id(self, version_id: str) -> DatasetVersion | None:
        row = await self.s.get(DatasetVersionRow, version_id)
        return _to_entity(row, _VERSION_FIELDS, DatasetVersion) if row else None

    async def latest(self, dataset_id: str) -> DatasetVersion | None:
        stmt = (
            select(DatasetVersionRow)
            .where(DatasetVersionRow.dataset_id == dataset_id)
            .order_by(DatasetVersionRow.version_no.desc())
            .limit(1)
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, DatasetVersion) if row else None

    async def list(self, dataset_id: str, limit: int, cursor: str | None) -> Page:
        stmt = (
            select(DatasetVersionRow)
            .where(DatasetVersionRow.dataset_id == dataset_id)
            .order_by(DatasetVersionRow.version_no.desc())
        )
        if cursor:
            stmt = stmt.where(
                DatasetVersionRow.version_no < int(decode_cursor(cursor)["v"])
            )
        rows = (await self.s.execute(stmt.limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        items = rows[:limit]
        return Page(
            items=[_to_entity(r, _VERSION_FIELDS, DatasetVersion) for r in items],
            next_cursor=(
                encode_cursor({"v": items[-1].version_no}) if has_more and items else None
            ),
            has_more=has_more,
        )

    async def list_all(self, dataset_id: str) -> list[DatasetVersion]:
        stmt = (
            select(DatasetVersionRow)
            .where(DatasetVersionRow.dataset_id == dataset_id)
            .order_by(DatasetVersionRow.version_no.asc())
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _VERSION_FIELDS, DatasetVersion) for r in rows]

    async def by_snapshot(self, dataset_id: str, snapshot_id: int) -> DatasetVersion | None:
        stmt = select(DatasetVersionRow).where(
            DatasetVersionRow.dataset_id == dataset_id,
            DatasetVersionRow.iceberg_snapshot_id == snapshot_id,
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, DatasetVersion) if row else None

    async def by_produced_by(self, produced_by_urn: str) -> DatasetVersion | None:
        stmt = select(DatasetVersionRow).where(
            DatasetVersionRow.produced_by_urn == produced_by_urn
        )
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, _VERSION_FIELDS, DatasetVersion) if row else None

    async def next_version_no(self, dataset_id: str) -> int:
        # BR-2: per-dataset advisory lock serializes concurrent registrations
        await self.s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:did, 42))"),
            {"did": dataset_id},
        )
        result = await self.s.execute(
            select(func.coalesce(func.max(DatasetVersionRow.version_no), 0)).where(
                DatasetVersionRow.dataset_id == dataset_id
            )
        )
        return int(result.scalar_one()) + 1

    async def update(self, version: DatasetVersion) -> None:
        row = await self.s.get(DatasetVersionRow, version.id)
        if row is not None:
            _apply(row, version, _VERSION_FIELDS)
            await self.s.flush()


class SqlProfileRepo:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def add(self, profile: Profile) -> None:
        row = ProfileRow()
        _apply(row, profile, _PROFILE_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get(self, profile_id: str) -> Profile | None:
        row = await self.s.get(ProfileRow, profile_id)
        return _to_entity(row, _PROFILE_FIELDS, Profile) if row else None

    async def update(self, profile: Profile) -> None:
        row = await self.s.get(ProfileRow, profile.id)
        if row is not None:
            _apply(row, profile, _PROFILE_FIELDS)
            await self.s.flush()

    async def count_since(self, dataset_id: str, since: datetime) -> int:
        result = await self.s.execute(
            select(func.count()).select_from(ProfileRow).where(
                ProfileRow.dataset_id == dataset_id, ProfileRow.created_at >= since
            )
        )
        return int(result.scalar_one())

    async def running_started_before(self, cutoff: datetime) -> list[Profile]:
        stmt = select(ProfileRow).where(
            ProfileRow.status.in_(["pending", "running"]),
            func.coalesce(ProfileRow.started_at, ProfileRow.created_at) < cutoff,
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _PROFILE_FIELDS, Profile) for r in rows]


class SqlLineageRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def upsert(self, edge: LineageEdge) -> tuple[LineageEdge, bool]:
        values = {f: getattr(edge, f) for f in _EDGE_FIELDS}
        stmt = (
            pg_insert(LineageEdgeRow)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "from_urn", "to_urn", "activity", "run_urn"]
            )
            .returning(LineageEdgeRow.id)
        )
        created = (await self.s.execute(stmt)).scalar() is not None
        if not created:
            existing = (
                await self.s.execute(
                    select(LineageEdgeRow).where(
                        LineageEdgeRow.from_urn == edge.from_urn,
                        LineageEdgeRow.to_urn == edge.to_urn,
                        LineageEdgeRow.activity == edge.activity,
                        LineageEdgeRow.run_urn.is_(None)
                        if edge.run_urn is None
                        else LineageEdgeRow.run_urn == edge.run_urn,
                    )
                )
            ).scalars().first()
            if existing:
                return _to_entity(existing, _EDGE_FIELDS, LineageEdge), False
        return edge, created

    async def edges_touching(
        self, urns: set[str], direction: str, activities: list[str] | None
    ) -> list[LineageEdge]:
        if not urns:
            return []
        conds = []
        if direction in ("downstream", "both"):
            conds.append(LineageEdgeRow.from_urn.in_(urns))
        if direction in ("upstream", "both"):
            conds.append(LineageEdgeRow.to_urn.in_(urns))
        cond = conds[0] if len(conds) == 1 else conds[0] | conds[1]
        stmt = select(LineageEdgeRow).where(cond)
        if activities:
            stmt = stmt.where(LineageEdgeRow.activity.in_(activities))
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _EDGE_FIELDS, LineageEdge) for r in rows]

    async def edges_from(self, urns: set[str]) -> list[LineageEdge]:
        return await self.edges_touching(urns, "downstream", None)

    async def trained_edges_since(self, since: datetime) -> list[LineageEdge]:
        stmt = select(LineageEdgeRow).where(
            LineageEdgeRow.activity == "trained", LineageEdgeRow.occurred_at >= since
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _EDGE_FIELDS, LineageEdge) for r in rows]


class SqlOutboxRepo:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def add(self, topic: str, envelope: dict) -> None:
        self.s.add(
            OutboxRow(
                id=str(uuid7()),
                tenant_id=self.tenant_id,
                topic=topic,
                event_type=envelope["event_type"],
                payload=envelope,
                created_at=utcnow(),
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
        return {
            "request_hash": row.request_hash,
            "status_code": row.status_code,
            "body": row.response_body,
        }

    async def put(self, key: str, request_hash: str, status_code: int, body: dict) -> None:
        self.s.add(
            IdempotencyKeyRow(
                tenant_id=self.tenant_id,
                key=key,
                request_hash=request_hash,
                status_code=status_code,
                response_body=body,
                created_at=utcnow(),
            )
        )
        await self.s.flush()


_ERCONFIG_FIELDS = [f.name for f in dataclasses.fields(EntityResolutionConfig)]
_ERRUN_FIELDS = [f.name for f in dataclasses.fields(EntityResolutionRun)]
_RESOLVED_FIELDS = [f.name for f in dataclasses.fields(ResolvedEntity)]
_MEMBER_FIELDS = [f.name for f in dataclasses.fields(ResolvedEntityMember)]
_CAND_FIELDS = [f.name for f in dataclasses.fields(EntityMergeCandidate)]


class SqlEntityResolutionRepo:
    """BRD 56 inc2 persistence: versioned configs, runs, resolved clusters +
    lineage, and the four-eyes merge-candidate queue. RLS scopes every read to
    the UoW's tenant via app.tenant_id."""

    def __init__(self, session: AsyncSession, tenant_id: str):
        self.s = session
        self.tenant_id = tenant_id

    async def next_config_version(self, dataset_id: str, entity_type: str) -> int:
        stmt = select(func.max(ResolutionConfigRow.version_no)).where(
            ResolutionConfigRow.dataset_id == dataset_id,
            ResolutionConfigRow.entity_type == entity_type,
        )
        cur = (await self.s.execute(stmt)).scalar_one_or_none()
        return (cur or 0) + 1

    async def add_config(self, cfg: EntityResolutionConfig) -> None:
        row = ResolutionConfigRow()
        _apply(row, cfg, _ERCONFIG_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get_config(self, config_id: str) -> EntityResolutionConfig | None:
        row = await self.s.get(ResolutionConfigRow, config_id)
        return _to_entity(row, _ERCONFIG_FIELDS, EntityResolutionConfig) if row else None

    async def add_run(self, run: EntityResolutionRun) -> None:
        row = ResolutionRunRow()
        _apply(row, run, _ERRUN_FIELDS)
        self.s.add(row)
        await self.s.flush()

    async def get_run(self, run_id: str) -> EntityResolutionRun | None:
        row = await self.s.get(ResolutionRunRow, run_id)
        return _to_entity(row, _ERRUN_FIELDS, EntityResolutionRun) if row else None

    async def list_runs(self, dataset_id: str, limit: int = 50) -> list[EntityResolutionRun]:
        stmt = (
            select(ResolutionRunRow)
            .where(ResolutionRunRow.dataset_id == dataset_id)
            .order_by(ResolutionRunRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _ERRUN_FIELDS, EntityResolutionRun) for r in rows]

    async def add_resolved_entities(self, items: list[ResolvedEntity]) -> None:
        for e in items:
            row = ResolvedEntityRow()
            _apply(row, e, _RESOLVED_FIELDS)
            self.s.add(row)
        await self.s.flush()

    async def add_members(self, items: list[ResolvedEntityMember]) -> None:
        for m in items:
            row = ResolvedEntityMemberRow()
            _apply(row, m, _MEMBER_FIELDS)
            self.s.add(row)
        await self.s.flush()

    async def add_candidates(self, items: list[EntityMergeCandidate]) -> None:
        for c in items:
            row = MergeCandidateRow()
            _apply(row, c, _CAND_FIELDS)
            self.s.add(row)
        await self.s.flush()

    async def list_resolved_entities(self, run_id: str) -> list[ResolvedEntity]:
        stmt = select(ResolvedEntityRow).where(ResolvedEntityRow.run_id == run_id).order_by(
            ResolvedEntityRow.resolved_entity_id)
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _RESOLVED_FIELDS, ResolvedEntity) for r in rows]

    async def list_members(self, run_id: str) -> list[ResolvedEntityMember]:
        stmt = select(ResolvedEntityMemberRow).where(ResolvedEntityMemberRow.run_id == run_id)
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _MEMBER_FIELDS, ResolvedEntityMember) for r in rows]

    async def list_candidates(self, run_id: str,
                              status: str | None = None) -> list[EntityMergeCandidate]:
        stmt = select(MergeCandidateRow).where(MergeCandidateRow.run_id == run_id)
        if status:
            stmt = stmt.where(MergeCandidateRow.status == status)
        stmt = stmt.order_by(MergeCandidateRow.score.desc())
        rows = (await self.s.execute(stmt)).scalars().all()
        return [_to_entity(r, _CAND_FIELDS, EntityMergeCandidate) for r in rows]

    async def get_candidate(self, candidate_id: str) -> EntityMergeCandidate | None:
        row = await self.s.get(MergeCandidateRow, candidate_id)
        return _to_entity(row, _CAND_FIELDS, EntityMergeCandidate) if row else None

    async def set_candidate_proposal(self, candidate_id: str, proposal_id: str) -> None:
        await self.s.execute(
            update(MergeCandidateRow)
            .where(MergeCandidateRow.id == candidate_id)
            .values(proposal_id=proposal_id))
        await self.s.flush()

    async def decide_candidate(self, candidate_id: str, *, status: str,
                               decided_by: str, decided_at: datetime) -> None:
        await self.s.execute(
            update(MergeCandidateRow)
            .where(MergeCandidateRow.id == candidate_id)
            .values(status=status, decided_by=decided_by, decided_at=decided_at))
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
        self.datasets = SqlDatasetRepo(self._session)
        self.versions = SqlVersionRepo(self._session)
        self.profiles = SqlProfileRepo(self._session)
        self.lineage = SqlLineageRepo(self._session, self.tenant_id)
        self.outbox = SqlOutboxRepo(self._session, self.tenant_id)
        self.idempotency = SqlIdempotencyRepo(self._session, self.tenant_id)
        self.entity_resolution = SqlEntityResolutionRepo(self._session, self.tenant_id)
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
    """Durable consumer dedup on processed_events (Redis in prod).

    The marker is written by mark_processed only after the handler's effects are
    durable (handle-then-mark), so a failure mid-handling leaves no marker and
    the event is safely re-run on redelivery — the handler is idempotent, giving
    exactly-once effect (MASTER-FR-032).
    """

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            row = await session.get(ProcessedEventRow, event_id)
            return row is not None

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
