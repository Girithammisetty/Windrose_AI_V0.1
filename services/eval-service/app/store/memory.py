"""In-memory store for the unit/dev tier (MASTER wave-1 test double).

Reachable ONLY from unit tests and the dev container — never from the real
runtime, which uses the Postgres/RLS store. Mirrors the SQL UoW/repo API so
services are storage-agnostic. Enforces tenant isolation in software so unit-tier
isolation tests are meaningful without Postgres."""

from __future__ import annotations

import copy
from dataclasses import replace

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
from app.utils import decode_cursor, encode_cursor, new_id, utcnow


class MemoryState:
    def __init__(self):
        self.datasets: dict[str, Dataset] = {}
        self.cases: dict[str, EvalCase] = {}
        self.scorers: dict[str, Scorer] = {}
        self.suites: dict[str, Suite] = {}
        self.runs: dict[str, EvalRun] = {}
        self.case_results: dict[str, CaseResult] = {}
        self.gates: dict[str, GateResult] = {}
        self.canaries: dict[str, CanaryComparison] = {}
        self.slo: dict[str, SloRollup] = {}
        self.outbox: list[dict] = []
        self.processed: set[tuple[str, str]] = set()


def _tenant_scope(items, tenant_id):
    return [i for i in items if i.tenant_id == tenant_id]


class _DatasetRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def create(self, d: Dataset) -> Dataset:
        self.s.datasets[d.id] = d
        return d

    async def get(self, dataset_key, version) -> Dataset | None:
        for d in _tenant_scope(self.s.datasets.values(), self.t):
            if d.dataset_key == dataset_key and d.version == version:
                return d
        return None

    async def latest(self, dataset_key) -> Dataset | None:
        matches = [
            d
            for d in _tenant_scope(self.s.datasets.values(), self.t)
            if d.dataset_key == dataset_key
        ]
        return max(matches, key=lambda d: d.version) if matches else None

    async def update(self, d: Dataset) -> None:
        self.s.datasets[d.id] = d

    async def list(self, agent_key=None, limit=50, cursor=None) -> Page:
        items = sorted(
            _tenant_scope(self.s.datasets.values(), self.t),
            key=lambda d: (d.dataset_key, d.version),
        )
        if agent_key:
            items = [d for d in items if d.agent_key == agent_key]
        return _paginate(items, limit, cursor)


class _CaseRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def add(self, c: EvalCase) -> EvalCase:
        self.s.cases[c.id] = c
        return c

    async def get(self, case_id) -> EvalCase | None:
        c = self.s.cases.get(case_id)
        return c if c and c.tenant_id == self.t else None

    async def update(self, c: EvalCase) -> None:
        self.s.cases[c.id] = c

    async def find_by_source_ref(self, source_ref) -> EvalCase | None:
        for c in _tenant_scope(self.s.cases.values(), self.t):
            if c.source_ref == source_ref:
                return c
        return None

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
        items = _tenant_scope(self.s.cases.values(), self.t)
        if dataset_key:
            items = [c for c in items if c.dataset_key == dataset_key]
        if dataset_version is not None:
            items = [c for c in items if c.dataset_version == dataset_version]
        if status:
            items = [c for c in items if c.status == status]
        if source:
            items = [c for c in items if c.source == source]
        if tags:
            items = [c for c in items if set(tags).issubset(set(c.tags))]
        items = sorted(items, key=lambda c: c.id)
        return _paginate(items, limit, cursor)

    async def active_for(self, dataset_key, version) -> list[EvalCase]:
        return [
            c
            for c in _tenant_scope(self.s.cases.values(), self.t)
            if c.dataset_key == dataset_key
            and c.dataset_version == version
            and c.status == "active"
        ]

    async def count_active(self, dataset_key, version) -> int:
        return len(await self.active_for(dataset_key, version))


class _ScorerRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def upsert(self, sc: Scorer) -> Scorer:
        self.s.scorers[sc.id] = sc
        return sc

    async def get(self, scorer_key, version) -> Scorer | None:
        for sc in _tenant_scope(self.s.scorers.values(), self.t):
            if sc.scorer_key == scorer_key and sc.version == version:
                return sc
        return None

    async def latest(self, scorer_key) -> Scorer | None:
        m = [
            sc
            for sc in _tenant_scope(self.s.scorers.values(), self.t)
            if sc.scorer_key == scorer_key
        ]
        return max(m, key=lambda sc: sc.version) if m else None

    async def list(self, limit=200, cursor=None) -> Page:
        items = sorted(
            _tenant_scope(self.s.scorers.values(), self.t),
            key=lambda sc: (sc.scorer_key, sc.version),
        )
        return _paginate(items, limit, cursor)


class _SuiteRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def add(self, su: Suite) -> Suite:
        self.s.suites[su.id] = su
        return su

    async def update(self, su: Suite) -> None:
        self.s.suites[su.id] = su

    async def get(self, suite_id, version=None) -> Suite | None:
        m = [su for su in _tenant_scope(self.s.suites.values(), self.t) if su.suite_id == suite_id]
        if version is not None:
            m = [su for su in m if su.version == version]
        return max(m, key=lambda su: su.version) if m else None

    async def next_version(self, suite_id) -> int:
        m = [
            su.version
            for su in _tenant_scope(self.s.suites.values(), self.t)
            if su.suite_id == suite_id
        ]
        return (max(m) + 1) if m else 1


class _RunRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def add(self, r: EvalRun) -> EvalRun:
        self.s.runs[r.id] = r
        return r

    async def get(self, run_id) -> EvalRun | None:
        r = self.s.runs.get(run_id)
        return r if r and r.tenant_id == self.t else None

    async def update(self, r: EvalRun) -> None:
        self.s.runs[r.id] = r

    async def find_inflight(self, agent_key, content_digest, suite_version) -> EvalRun | None:
        for r in _tenant_scope(self.s.runs.values(), self.t):
            if (
                r.agent_key == agent_key
                and r.candidate.get("content_digest") == content_digest
                and r.suite_pins.get("suite_version") == suite_version
                and r.status in ("queued", "running", "scoring")
            ):
                return r
        return None

    async def list(self, agent_key=None, trigger=None, limit=50, cursor=None) -> Page:
        items = _tenant_scope(self.s.runs.values(), self.t)
        if agent_key:
            items = [r for r in items if r.agent_key == agent_key]
        if trigger:
            items = [r for r in items if r.trigger == trigger]
        items = sorted(items, key=lambda r: r.created_at, reverse=True)
        return _paginate(items, limit, cursor)


class _CaseResultRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def add_many(self, results: list[dict]) -> None:
        for r in results:
            cr = CaseResult(
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
            self.s.case_results[cr.id] = cr

    async def list_by_run(self, run_id) -> list[CaseResult]:
        return [
            r for r in _tenant_scope(self.s.case_results.values(), self.t) if r.run_id == run_id
        ]


class _GateRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def add(self, g: GateResult) -> GateResult:
        self.s.gates[g.gate_run_id] = g
        return g

    async def get(self, gate_run_id) -> GateResult | None:
        g = self.s.gates.get(gate_run_id)
        return g if g and g.tenant_id == self.t else None

    async def find(
        self, agent_key, content_digest, suite_id, suite_version, dataset_version
    ) -> GateResult | None:
        for g in _tenant_scope(self.s.gates.values(), self.t):
            if (
                g.agent_key == agent_key
                and g.content_digest == content_digest
                and g.suite_id == suite_id
                and g.suite_version == suite_version
                and g.dataset_version == dataset_version
            ):
                return g
        return None

    async def find_by_digest(self, agent_key, content_digest) -> list[GateResult]:
        return [
            g
            for g in _tenant_scope(self.s.gates.values(), self.t)
            if g.agent_key == agent_key and g.content_digest == content_digest
        ]


class _CanaryRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def add(self, c: CanaryComparison) -> CanaryComparison:
        self.s.canaries[c.comparison_id] = c
        return c

    async def get(self, comparison_id) -> CanaryComparison | None:
        c = self.s.canaries.get(comparison_id)
        return c if c and c.tenant_id == self.t else None

    async def update(self, c: CanaryComparison) -> None:
        self.s.canaries[c.comparison_id] = c

    async def list(self, agent_key=None, status=None, limit=50, cursor=None) -> Page:
        items = _tenant_scope(self.s.canaries.values(), self.t)
        if agent_key:
            items = [c for c in items if c.agent_key == agent_key]
        if status:
            items = [c for c in items if c.status == status]
        items = sorted(items, key=lambda c: c.created_at, reverse=True)
        return _paginate(items, limit, cursor)


class _SloRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    def _key(self, agent_key, agent_version, tenant_id, window, window_start):
        return (f"{agent_key}|{agent_version or ''}|{tenant_id or ''}"
                f"|{window}|{window_start.isoformat()}")

    async def get_or_create(
        self, agent_key, agent_version, tenant_id, window, window_start
    ) -> SloRollup:
        k = self._key(agent_key, agent_version, tenant_id, window, window_start)
        row = self.s.slo.get(k)
        if row is None:
            from app.domain.slo import empty_counters

            row = SloRollup(
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
            self.s.slo[k] = row
        return row

    async def save(self, row: SloRollup) -> None:
        k = self._key(row.agent_key, row.agent_version, row.tenant_id, row.window, row.window_start)
        self.s.slo[k] = row

    async def list(self, agent_key, tenant_id=None, window=None) -> list[SloRollup]:
        out = []
        for row in self.s.slo.values():
            if row.agent_key != agent_key:
                continue
            if window and row.window != window:
                continue
            if tenant_id is not None and row.tenant_id not in (tenant_id, None):
                continue
            out.append(row)
        return out


class _OutboxRepo:
    def __init__(self, state, tenant):
        self.s, self.t = state, tenant

    async def add(self, topic, envelope) -> None:
        self.s.outbox.append({"topic": topic, "payload": envelope, "tenant_id": self.t})


class MemoryUnitOfWork:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id
        self.datasets = _DatasetRepo(state, tenant_id)
        self.cases = _CaseRepo(state, tenant_id)
        self.scorers = _ScorerRepo(state, tenant_id)
        self.suites = _SuiteRepo(state, tenant_id)
        self.runs = _RunRepo(state, tenant_id)
        self.case_results = _CaseResultRepo(state, tenant_id)
        self.gates = _GateRepo(state, tenant_id)
        self.canaries = _CanaryRepo(state, tenant_id)
        self.slo = _SloRepo(state, tenant_id)
        self.outbox = _OutboxRepo(state, tenant_id)
        self._snapshot = None

    async def __aenter__(self):
        # copy-on-write snapshot for rollback on error
        self._snapshot = (
            dict(self.state.datasets),
            dict(self.state.cases),
            dict(self.state.scorers),
            dict(self.state.suites),
            dict(self.state.runs),
            dict(self.state.case_results),
            dict(self.state.gates),
            dict(self.state.canaries),
            dict(self.state.slo),
            list(self.state.outbox),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None and self._snapshot is not None:
            (
                self.state.datasets,
                self.state.cases,
                self.state.scorers,
                self.state.suites,
                self.state.runs,
                self.state.case_results,
                self.state.gates,
                self.state.canaries,
                self.state.slo,
                self.state.outbox,
            ) = (
                self._snapshot[0],
                self._snapshot[1],
                self._snapshot[2],
                self._snapshot[3],
                self._snapshot[4],
                self._snapshot[5],
                self._snapshot[6],
                self._snapshot[7],
                self._snapshot[8],
                self._snapshot[9],
            )

    async def commit(self):
        self._snapshot = None


def memory_uow_factory(state: MemoryState):
    def factory(tenant_id: str) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(state, tenant_id)

    return factory


class InMemoryDedupStore:
    def __init__(self, state: MemoryState):
        self.state = state

    async def already_processed(self, tenant_id, event_id) -> bool:
        return (tenant_id, event_id) in self.state.processed

    async def mark_processed(self, tenant_id, event_id) -> None:
        self.state.processed.add((tenant_id, event_id))


def _paginate(items: list, limit: int, cursor: str | None) -> Page:
    offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
    window = items[offset : offset + limit]
    has_more = offset + limit < len(items)
    return Page(
        items=[copy.copy(i) for i in window],
        next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
        has_more=has_more,
    )


__all__ = ["MemoryState", "MemoryUnitOfWork", "memory_uow_factory", "InMemoryDedupStore", "replace"]
