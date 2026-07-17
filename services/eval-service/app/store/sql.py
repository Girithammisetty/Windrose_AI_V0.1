"""Postgres store + RLS-bound unit of work (runtime).

Every tenant UoW opens a transaction and sets ``app.tenant_id`` so Postgres RLS
(MASTER-FR-001) applies to the non-privileged ``eval_app`` role. The outbox
dispatcher uses a worker session (``app.worker=true``)."""

from __future__ import annotations

import dataclasses
import os

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.entities import (
    CanaryComparison,
    CaseResult,
    Dataset,
    EvalCase,
    EvalRun,
    GateResult,
    Page,
    Scorer,
    SloRollup,
    Suite,
)
from app.store.orm import (
    CanaryRow,
    CaseResultRow,
    DatasetRow,
    EvalCaseRow,
    EvalRunRow,
    GateResultRow,
    OutboxRow,
    ProcessedEventRow,
    ScorerRow,
    SloRollupRow,
    SuiteRow,
)
from app.utils import decode_cursor, encode_cursor, new_id, utcnow

_FIELDS = {
    Dataset: [f.name for f in dataclasses.fields(Dataset)],
    EvalCase: [f.name for f in dataclasses.fields(EvalCase)],
    Scorer: [f.name for f in dataclasses.fields(Scorer)],
    Suite: [f.name for f in dataclasses.fields(Suite)],
    EvalRun: [f.name for f in dataclasses.fields(EvalRun)],
    CaseResult: [f.name for f in dataclasses.fields(CaseResult)],
    GateResult: [f.name for f in dataclasses.fields(GateResult)],
    CanaryComparison: [f.name for f in dataclasses.fields(CanaryComparison)],
    SloRollup: [f.name for f in dataclasses.fields(SloRollup)],
}


def make_engine(database_url: str):
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    )


def _to_entity(row, cls):
    return cls(**{f: getattr(row, f) for f in _FIELDS[cls]})


def _apply(row, entity, cls):
    for f in _FIELDS[cls]:
        setattr(row, f, getattr(entity, f))


def _paginate_rows(rows, cls, limit, offset):
    has_more = len(rows) > limit
    items = [_to_entity(r, cls) for r in rows[:limit]]
    return Page(
        items=items,
        next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
        has_more=has_more,
    )


class _DatasetRepo:
    def __init__(self, s):
        self.s = s

    async def create(self, d: Dataset) -> Dataset:
        row = DatasetRow()
        _apply(row, d, Dataset)
        self.s.add(row)
        await self.s.flush()
        return d

    async def get(self, dataset_key, version) -> Dataset | None:
        row = (
            (
                await self.s.execute(
                    select(DatasetRow).where(
                        DatasetRow.dataset_key == dataset_key, DatasetRow.version == version
                    )
                )
            )
            .scalars()
            .first()
        )
        return _to_entity(row, Dataset) if row else None

    async def latest(self, dataset_key) -> Dataset | None:
        row = (
            (
                await self.s.execute(
                    select(DatasetRow)
                    .where(DatasetRow.dataset_key == dataset_key)
                    .order_by(DatasetRow.version.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        return _to_entity(row, Dataset) if row else None

    async def update(self, d: Dataset) -> None:
        row = await self.s.get(DatasetRow, d.id)
        if row:
            _apply(row, d, Dataset)
            await self.s.flush()

    async def list(self, agent_key=None, limit=50, cursor=None) -> Page:
        stmt = select(DatasetRow)
        if agent_key:
            stmt = stmt.where(DatasetRow.agent_key == agent_key)
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        stmt = (
            stmt.order_by(DatasetRow.dataset_key, DatasetRow.version)
            .offset(offset)
            .limit(limit + 1)
        )
        rows = (await self.s.execute(stmt)).scalars().all()
        return _paginate_rows(rows, Dataset, limit, offset)


class _CaseRepo:
    def __init__(self, s):
        self.s = s

    async def add(self, c: EvalCase) -> EvalCase:
        row = EvalCaseRow()
        _apply(row, c, EvalCase)
        self.s.add(row)
        await self.s.flush()
        return c

    async def get(self, case_id) -> EvalCase | None:
        row = await self.s.get(EvalCaseRow, case_id)
        return _to_entity(row, EvalCase) if row else None

    async def update(self, c: EvalCase) -> None:
        row = await self.s.get(EvalCaseRow, c.id)
        if row:
            _apply(row, c, EvalCase)
            await self.s.flush()

    async def find_by_source_ref(self, source_ref) -> EvalCase | None:
        row = (
            (
                await self.s.execute(
                    select(EvalCaseRow).where(EvalCaseRow.source_ref == source_ref).limit(1)
                )
            )
            .scalars()
            .first()
        )
        return _to_entity(row, EvalCase) if row else None

    async def list(
        self,
        dataset_key=None,
        dataset_version=None,
        status=None,
        source=None,
        tags=None,
        limit=50,
        cursor=None,
    ) -> Page:
        stmt = select(EvalCaseRow)
        if dataset_key:
            stmt = stmt.where(EvalCaseRow.dataset_key == dataset_key)
        if dataset_version is not None:
            stmt = stmt.where(EvalCaseRow.dataset_version == dataset_version)
        if status:
            stmt = stmt.where(EvalCaseRow.status == status)
        if source:
            stmt = stmt.where(EvalCaseRow.source == source)
        if tags:
            stmt = stmt.where(EvalCaseRow.tags.contains(tags))
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        stmt = stmt.order_by(EvalCaseRow.id).offset(offset).limit(limit + 1)
        rows = (await self.s.execute(stmt)).scalars().all()
        return _paginate_rows(rows, EvalCase, limit, offset)

    async def active_for(self, dataset_key, version) -> list[EvalCase]:
        rows = (
            (
                await self.s.execute(
                    select(EvalCaseRow).where(
                        EvalCaseRow.dataset_key == dataset_key,
                        EvalCaseRow.dataset_version == version,
                        EvalCaseRow.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_to_entity(r, EvalCase) for r in rows]

    async def count_active(self, dataset_key, version) -> int:
        return int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(EvalCaseRow)
                    .where(
                        EvalCaseRow.dataset_key == dataset_key,
                        EvalCaseRow.dataset_version == version,
                        EvalCaseRow.status == "active",
                    )
                )
            ).scalar_one()
        )


class _ScorerRepo:
    def __init__(self, s):
        self.s = s

    async def upsert(self, sc: Scorer) -> Scorer:
        existing = (
            (
                await self.s.execute(
                    select(ScorerRow).where(
                        ScorerRow.scorer_key == sc.scorer_key, ScorerRow.version == sc.version
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing:
            _apply(existing, sc, Scorer)
        else:
            row = ScorerRow()
            _apply(row, sc, Scorer)
            self.s.add(row)
        await self.s.flush()
        return sc

    async def get(self, scorer_key, version) -> Scorer | None:
        row = (
            (
                await self.s.execute(
                    select(ScorerRow).where(
                        ScorerRow.scorer_key == scorer_key, ScorerRow.version == version
                    )
                )
            )
            .scalars()
            .first()
        )
        return _to_entity(row, Scorer) if row else None

    async def latest(self, scorer_key) -> Scorer | None:
        row = (
            (
                await self.s.execute(
                    select(ScorerRow)
                    .where(ScorerRow.scorer_key == scorer_key)
                    .order_by(ScorerRow.version.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        return _to_entity(row, Scorer) if row else None

    async def list(self, limit=200, cursor=None) -> Page:
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        rows = (
            (
                await self.s.execute(
                    select(ScorerRow)
                    .order_by(ScorerRow.scorer_key, ScorerRow.version)
                    .offset(offset)
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        return _paginate_rows(rows, Scorer, limit, offset)


class _SuiteRepo:
    def __init__(self, s):
        self.s = s

    async def add(self, su: Suite) -> Suite:
        row = SuiteRow()
        _apply(row, su, Suite)
        self.s.add(row)
        await self.s.flush()
        return su

    async def update(self, su: Suite) -> None:
        row = (
            (await self.s.execute(select(SuiteRow).where(SuiteRow.id == su.id))).scalars().first()
        )
        if row is not None:
            _apply(row, su, Suite)
            await self.s.flush()

    async def get(self, suite_id, version=None) -> Suite | None:
        stmt = select(SuiteRow).where(SuiteRow.suite_id == suite_id)
        if version is not None:
            stmt = stmt.where(SuiteRow.version == version)
        stmt = stmt.order_by(SuiteRow.version.desc()).limit(1)
        row = (await self.s.execute(stmt)).scalars().first()
        return _to_entity(row, Suite) if row else None

    async def next_version(self, suite_id) -> int:
        m = (
            await self.s.execute(
                select(func.coalesce(func.max(SuiteRow.version), 0)).where(
                    SuiteRow.suite_id == suite_id
                )
            )
        ).scalar_one()
        return int(m) + 1


class _RunRepo:
    def __init__(self, s):
        self.s = s

    async def add(self, r: EvalRun) -> EvalRun:
        row = EvalRunRow()
        _apply(row, r, EvalRun)
        self.s.add(row)
        await self.s.flush()
        return r

    async def get(self, run_id) -> EvalRun | None:
        row = await self.s.get(EvalRunRow, run_id)
        return _to_entity(row, EvalRun) if row else None

    async def update(self, r: EvalRun) -> None:
        row = await self.s.get(EvalRunRow, r.id)
        if row:
            _apply(row, r, EvalRun)
            await self.s.flush()

    async def find_inflight(self, agent_key, content_digest, suite_version) -> EvalRun | None:
        rows = (
            (
                await self.s.execute(
                    select(EvalRunRow).where(
                        EvalRunRow.agent_key == agent_key,
                        EvalRunRow.status.in_(["queued", "running", "scoring"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if (
                row.candidate.get("content_digest") == content_digest
                and row.suite_pins.get("suite_version") == suite_version
            ):
                return _to_entity(row, EvalRun)
        return None

    async def list(self, agent_key=None, trigger=None, limit=50, cursor=None) -> Page:
        stmt = select(EvalRunRow)
        if agent_key:
            stmt = stmt.where(EvalRunRow.agent_key == agent_key)
        if trigger:
            stmt = stmt.where(EvalRunRow.trigger == trigger)
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        stmt = stmt.order_by(EvalRunRow.created_at.desc()).offset(offset).limit(limit + 1)
        rows = (await self.s.execute(stmt)).scalars().all()
        return _paginate_rows(rows, EvalRun, limit, offset)


class _CaseResultRepo:
    def __init__(self, s, tenant):
        self.s, self.t = s, tenant

    async def add_many(self, results: list[dict]) -> None:
        for r in results:
            self.s.add(
                CaseResultRow(
                    id=r.get("id") or new_id(),
                    tenant_id=self.t,
                    run_id=r["run_id"],
                    case_id=r["case_id"],
                    scorer_key=r["scorer_key"],
                    scorer_version=r.get("scorer_version", 1),
                    score=r["score"],
                    passed=r["passed"],
                    details=r.get("details", {}),
                    trace_ref=r.get("trace_ref"),
                    latency_ms=r.get("latency_ms"),
                    cost_usd=r.get("cost_usd", 0.0),
                    weight=r.get("weight", 1.0),
                    created_at=r.get("created_at") or utcnow(),
                )
            )
        await self.s.flush()

    async def list_by_run(self, run_id) -> list[CaseResult]:
        rows = (
            (await self.s.execute(select(CaseResultRow).where(CaseResultRow.run_id == run_id)))
            .scalars()
            .all()
        )
        return [_to_entity(r, CaseResult) for r in rows]


class _GateRepo:
    def __init__(self, s):
        self.s = s

    async def add(self, g: GateResult) -> GateResult:
        row = GateResultRow()
        _apply(row, g, GateResult)
        self.s.add(row)
        await self.s.flush()
        return g

    async def get(self, gate_run_id) -> GateResult | None:
        row = (
            (
                await self.s.execute(
                    select(GateResultRow).where(GateResultRow.gate_run_id == gate_run_id).limit(1)
                )
            )
            .scalars()
            .first()
        )
        return _to_entity(row, GateResult) if row else None

    async def find(
        self, agent_key, content_digest, suite_id, suite_version, dataset_version
    ) -> GateResult | None:
        row = (
            (
                await self.s.execute(
                    select(GateResultRow)
                    .where(
                        GateResultRow.agent_key == agent_key,
                        GateResultRow.content_digest == content_digest,
                        GateResultRow.suite_id == suite_id,
                        GateResultRow.suite_version == suite_version,
                        GateResultRow.dataset_version == dataset_version,
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        return _to_entity(row, GateResult) if row else None

    async def find_by_digest(self, agent_key, content_digest) -> list[GateResult]:
        rows = (
            (
                await self.s.execute(
                    select(GateResultRow).where(
                        GateResultRow.agent_key == agent_key,
                        GateResultRow.content_digest == content_digest,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_to_entity(r, GateResult) for r in rows]


class _CanaryRepo:
    def __init__(self, s):
        self.s = s

    async def add(self, c: CanaryComparison) -> CanaryComparison:
        row = CanaryRow()
        _apply(row, c, CanaryComparison)
        self.s.add(row)
        await self.s.flush()
        return c

    async def get(self, comparison_id) -> CanaryComparison | None:
        row = (
            (
                await self.s.execute(
                    select(CanaryRow).where(CanaryRow.comparison_id == comparison_id).limit(1)
                )
            )
            .scalars()
            .first()
        )
        return _to_entity(row, CanaryComparison) if row else None

    async def update(self, c: CanaryComparison) -> None:
        row = (
            (
                await self.s.execute(
                    select(CanaryRow).where(CanaryRow.comparison_id == c.comparison_id).limit(1)
                )
            )
            .scalars()
            .first()
        )
        if row:
            _apply(row, c, CanaryComparison)
            await self.s.flush()

    async def list(self, agent_key=None, status=None, limit=50, cursor=None) -> Page:
        stmt = select(CanaryRow)
        if agent_key:
            stmt = stmt.where(CanaryRow.agent_key == agent_key)
        if status:
            stmt = stmt.where(CanaryRow.status == status)
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        stmt = stmt.order_by(CanaryRow.created_at.desc()).offset(offset).limit(limit + 1)
        rows = (await self.s.execute(stmt)).scalars().all()
        return _paginate_rows(rows, CanaryComparison, limit, offset)


class _SloRepo:
    def __init__(self, s, tenant):
        self.s, self.t = s, tenant

    async def get_or_create(
        self, agent_key, agent_version, tenant_id, window, window_start
    ) -> SloRollup:
        stmt = select(SloRollupRow).where(
            SloRollupRow.agent_key == agent_key,
            SloRollupRow.window == window,
            SloRollupRow.window_start == window_start,
            SloRollupRow.agent_version == agent_version,
        )
        if tenant_id is None:
            stmt = stmt.where(SloRollupRow.tenant_id.is_(None))
        else:
            stmt = stmt.where(SloRollupRow.tenant_id == tenant_id)
        row = (await self.s.execute(stmt.limit(1))).scalars().first()
        if row is None:
            from app.domain.slo import empty_counters

            row = SloRollupRow(
                id=new_id(),
                tenant_id=tenant_id,
                agent_key=agent_key,
                agent_version=agent_version,
                window=window,
                window_start=window_start,
                counters=empty_counters(),
                targets={},
                sample_n=0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            self.s.add(row)
            await self.s.flush()
        return _to_entity(row, SloRollup)

    async def save(self, rollup: SloRollup) -> None:
        stmt = select(SloRollupRow).where(SloRollupRow.id == rollup.id)
        row = (await self.s.execute(stmt)).scalars().first()
        if row:
            _apply(row, rollup, SloRollup)
            await self.s.flush()

    async def list(self, agent_key, tenant_id=None, window=None) -> list[SloRollup]:
        stmt = select(SloRollupRow).where(SloRollupRow.agent_key == agent_key)
        if window:
            stmt = stmt.where(SloRollupRow.window == window)
        rows = (await self.s.execute(stmt)).scalars().all()
        out = []
        for r in rows:
            if tenant_id is not None and r.tenant_id not in (tenant_id, None):
                continue
            out.append(_to_entity(r, SloRollup))
        return out


class _OutboxRepo:
    def __init__(self, s, tenant):
        self.s, self.t = s, tenant

    async def add(self, topic, envelope) -> None:
        self.s.add(
            OutboxRow(
                id=new_id(),
                tenant_id=self.t,
                topic=topic,
                event_type=envelope["event_type"],
                payload=envelope,
                created_at=utcnow(),
            )
        )
        await self.s.flush()


class SqlUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker, tenant_id: str):
        self.tenant_id = tenant_id
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> SqlUnitOfWork:
        self._session = self._session_factory()
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": self.tenant_id}
        )
        s = self._session
        self.datasets = _DatasetRepo(s)
        self.cases = _CaseRepo(s)
        self.scorers = _ScorerRepo(s)
        self.suites = _SuiteRepo(s)
        self.runs = _RunRepo(s)
        self.case_results = _CaseResultRepo(s, self.tenant_id)
        self.gates = _GateRepo(s)
        self.canaries = _CanaryRepo(s)
        self.slo = _SloRepo(s, self.tenant_id)
        self.outbox = _OutboxRepo(s, self.tenant_id)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                await self._session.commit()
                await self._session.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": self.tenant_id}
                )
            else:
                await self._session.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": self.tenant_id}
        )


def sql_uow_factory(session_factory: async_sessionmaker):
    def factory(tenant_id: str) -> SqlUnitOfWork:
        return SqlUnitOfWork(session_factory, tenant_id)

    return factory


class SqlDedupStore:
    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def already_processed(self, tenant_id, event_id) -> bool:
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            return (await session.get(ProcessedEventRow, event_id)) is not None

    async def mark_processed(self, tenant_id, event_id) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            await session.execute(
                pg_insert(ProcessedEventRow)
                .values(event_id=event_id, tenant_id=tenant_id, created_at=utcnow())
                .on_conflict_do_nothing(index_elements=["event_id"])
            )
            await session.commit()


class OutboxDispatcher:
    """Polls unpublished outbox rows and publishes to the bus (MASTER-FR-034)."""

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
