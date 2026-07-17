"""In-memory unit-of-work for the unit/dev tier (never reachable from real runtime).

Mirrors the SqlUnitOfWork surface with dict-backed repos so domain-service unit
tests run without Postgres. Test double only (CONVENTIONS.md).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.domain.entities import InferenceJob, LineageEdge, ScoringSchedule
from app.domain.enums import JobStatus
from app.domain.ports import Filters, Page, ResolvedDataset
from app.utils import decode_cursor, encode_cursor, utcnow, uuid7


class MemoryState:
    def __init__(self) -> None:
        self.jobs: dict[str, InferenceJob] = {}
        self.schedules: dict[str, ScoringSchedule] = {}
        self.queue: list[tuple[str, str, datetime]] = []  # (tenant, job_id, enqueued_at)
        self.inputs: dict[str, ResolvedDataset] = {}
        self.output_datasets: dict[str, dict] = {}  # id -> {..}
        self.output_versions: list[dict] = []
        self.lineage: list[LineageEdge] = []
        self.outbox: list[tuple[str, dict]] = []
        self.idempotency: dict[tuple[str, str], dict] = {}


def _clone(obj):
    return replace(obj)


class _Jobs:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id

    def _visible(self, job: InferenceJob) -> bool:
        return job.tenant_id == self.tenant_id

    async def add(self, job: InferenceJob) -> None:
        self.state.jobs[job.id] = _clone(job)

    async def get(self, job_id: str, include_deleted: bool = False) -> InferenceJob | None:
        job = self.state.jobs.get(job_id)
        if job is None or not self._visible(job):
            return None
        if job.deleted_at is not None and not include_deleted:
            return None
        return _clone(job)

    async def update(self, job: InferenceJob) -> None:
        if job.id in self.state.jobs:
            self.state.jobs[job.id] = _clone(job)

    async def get_by_name(self, workspace_id: str, name: str) -> InferenceJob | None:
        for job in self.state.jobs.values():
            if (
                self._visible(job)
                and job.workspace_id == workspace_id
                and job.name == name
                and job.deleted_at is None
                and job.schedule_id is None
            ):
                return _clone(job)
        return None

    async def by_pipeline_run_urn(self, run_urn: str) -> InferenceJob | None:
        for job in self.state.jobs.values():
            if self._visible(job) and job.pipeline_run_urn == run_urn:
                return _clone(job)
        return None

    async def count_active(self, workspace_id: str | None = None) -> int:
        active = {JobStatus.submitted, JobStatus.running, JobStatus.finalizing,
                  JobStatus.cancelling}
        return sum(
            1 for j in self.state.jobs.values()
            if self._visible(j) and JobStatus(j.status) in active
        )

    async def list(self, filters: Filters, sort: str, limit: int, cursor: str | None) -> Page:
        rows = [
            j for j in self.state.jobs.values()
            if self._visible(j) and j.deleted_at is None
        ]
        if filters.status is not None:
            rows = [j for j in rows if j.status == filters.status]
        if filters.model_version_urn:
            rows = [j for j in rows if j.model_version_urn == filters.model_version_urn]
        if filters.schedule_id:
            rows = [j for j in rows if j.schedule_id == filters.schedule_id]
        descending = not sort.startswith("created_at")
        rows.sort(key=lambda j: (j.created_at, j.id), reverse=descending)
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        window = rows[offset:offset + limit]
        has_more = offset + limit < len(rows)
        return Page(
            items=[_clone(j) for j in window],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def running_started_before(self, cutoff: datetime) -> list[InferenceJob]:
        active = {JobStatus.submitted, JobStatus.running, JobStatus.finalizing,
                  JobStatus.cancelling}
        return [
            _clone(j) for j in self.state.jobs.values()
            if self._visible(j)
            and JobStatus(j.status) in active
            and (j.submitted_at or j.created_at) < cutoff
        ]

    async def queued_before(self, cutoff: datetime) -> list[InferenceJob]:
        return [
            _clone(j) for j in self.state.jobs.values()
            if self._visible(j)
            and JobStatus(j.status) == JobStatus.queued
            and (j.queued_at or j.created_at) < cutoff
        ]

    async def last_for_schedule(self, schedule_id: str) -> InferenceJob | None:
        matches = [
            j for j in self.state.jobs.values()
            if self._visible(j) and j.schedule_id == schedule_id
        ]
        if not matches:
            return None
        return _clone(max(matches, key=lambda j: j.created_at))


class _Schedules:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id

    def _visible(self, s: ScoringSchedule) -> bool:
        return s.tenant_id == self.tenant_id

    async def add(self, sch: ScoringSchedule) -> None:
        self.state.schedules[sch.id] = _clone(sch)

    async def get(self, sid: str, include_deleted: bool = False) -> ScoringSchedule | None:
        s = self.state.schedules.get(sid)
        if s is None or not self._visible(s):
            return None
        if s.deleted_at is not None and not include_deleted:
            return None
        return _clone(s)

    async def update(self, sch: ScoringSchedule) -> None:
        if sch.id in self.state.schedules:
            self.state.schedules[sch.id] = _clone(sch)

    async def get_by_name(self, workspace_id: str, name: str) -> ScoringSchedule | None:
        for s in self.state.schedules.values():
            if (self._visible(s) and s.workspace_id == workspace_id
                    and s.name == name and s.deleted_at is None):
                return _clone(s)
        return None

    async def count_enabled(self) -> int:
        return sum(
            1 for s in self.state.schedules.values()
            if self._visible(s) and s.enabled and s.deleted_at is None
        )

    async def list(self, limit: int, cursor: str | None) -> Page:
        rows = [
            s for s in self.state.schedules.values()
            if self._visible(s) and s.deleted_at is None
        ]
        rows.sort(key=lambda s: (s.created_at, s.id), reverse=True)
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        window = rows[offset:offset + limit]
        has_more = offset + limit < len(rows)
        return Page(
            items=[_clone(s) for s in window],
            next_cursor=encode_cursor({"o": offset + limit}) if has_more else None,
            has_more=has_more,
        )

    async def all_enabled(self) -> list[ScoringSchedule]:
        return [
            _clone(s) for s in self.state.schedules.values()
            if s.enabled and s.deleted_at is None
        ]


class _Queue:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id

    async def enqueue(self, job_id: str) -> None:
        self.state.queue.append((self.tenant_id, job_id, utcnow()))

    async def depth(self) -> int:
        return sum(1 for t, _, _ in self.state.queue if t == self.tenant_id)

    async def next_job_id(self) -> str | None:
        for t, job_id, _ in sorted(self.state.queue, key=lambda x: x[2]):
            if t == self.tenant_id:
                return job_id
        return None

    async def remove(self, job_id: str) -> None:
        self.state.queue = [x for x in self.state.queue if x[1] != job_id]


class _Inputs:
    def __init__(self, state: MemoryState):
        self.state = state

    async def get(self, urn: str, version: int | None = None) -> ResolvedDataset | None:
        return self.state.inputs.get(urn)

    async def upsert(self, *, urn: str, dataset_id: str, version_no: int, schema: dict,
                     storage_uri: str, row_count: int, tenant_id: str) -> None:
        self.state.inputs[urn] = ResolvedDataset(
            urn=urn, dataset_id=dataset_id, version=version_no, schema=schema,
            row_count=row_count, storage_uri=storage_uri,
        )


class _Outputs:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id

    async def find(self, workspace_id: str, name: str):
        for d in self.state.output_datasets.values():
            if (d["tenant_id"] == self.tenant_id and d["workspace_id"] == workspace_id
                    and d["name"] == name):
                return type("Row", (), d)
        return None

    async def version_for_job(self, job_id: str):
        for v in self.state.output_versions:
            if v["produced_by_job_id"] == job_id and v["tenant_id"] == self.tenant_id:
                return type("Row", (), v)
        return None

    async def create_dataset(self, *, workspace_id: str, name: str, urn: str,
                             owner_model_urn: str):
        did = str(uuid7())
        d = {
            "id": did, "tenant_id": self.tenant_id, "workspace_id": workspace_id,
            "name": name, "urn": urn, "owner_model_urn": owner_model_urn,
            "current_version": 0,
        }
        self.state.output_datasets[did] = d
        return type("Row", (), d)

    async def add_version(self, *, dataset_id: str, version_no: int, storage_uri: str,
                          snapshot_id: str, row_count: int, job_id: str) -> None:
        self.state.output_versions.append({
            "tenant_id": self.tenant_id, "dataset_id": dataset_id, "version_no": version_no,
            "storage_uri": storage_uri, "snapshot_id": snapshot_id, "row_count": row_count,
            "produced_by_job_id": job_id,
        })

    async def bump_version(self, row, version_no: int) -> None:
        self.state.output_datasets[row.id]["current_version"] = version_no


class _Lineage:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id

    async def upsert(self, edge: LineageEdge) -> bool:
        for e in self.state.lineage:
            if (e.tenant_id == edge.tenant_id and e.from_urn == edge.from_urn
                    and e.to_urn == edge.to_urn and e.activity == edge.activity
                    and e.run_urn == edge.run_urn):
                return False
        self.state.lineage.append(_clone(edge))
        return True

    async def edges_touching(self, urn: str, direction: str) -> list[LineageEdge]:
        out = []
        for e in self.state.lineage:
            if e.tenant_id != self.tenant_id:
                continue
            if direction in ("downstream", "both") and e.from_urn == urn:
                out.append(_clone(e))
            elif direction in ("upstream", "both") and e.to_urn == urn:
                out.append(_clone(e))
        return out


class _Outbox:
    def __init__(self, state: MemoryState):
        self.state = state

    async def add(self, topic: str, envelope: dict) -> None:
        self.state.outbox.append((topic, envelope))


class _Idempotency:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state = state
        self.tenant_id = tenant_id

    async def get(self, key: str) -> dict | None:
        return self.state.idempotency.get((self.tenant_id, key))

    async def put(self, key: str, request_hash: str, status_code: int, body: dict) -> None:
        self.state.idempotency[(self.tenant_id, key)] = {
            "request_hash": request_hash, "status_code": status_code, "body": body,
        }


class MemoryUnitOfWork:
    def __init__(self, state: MemoryState, tenant_id: str, *, worker: bool = False):
        self.state = state
        self.tenant_id = tenant_id
        self._worker = worker

    async def __aenter__(self) -> MemoryUnitOfWork:
        self.jobs = _Jobs(self.state, self.tenant_id)
        self.schedules = _Schedules(self.state, self.tenant_id)
        self.queue = _Queue(self.state, self.tenant_id)
        self.inputs = _Inputs(self.state)
        self.outputs = _Outputs(self.state, self.tenant_id)
        self.lineage = _Lineage(self.state, self.tenant_id)
        self.outbox = _Outbox(self.state)
        self.idempotency = _Idempotency(self.state, self.tenant_id)
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def memory_uow_factory(state: MemoryState):
    def factory(tenant_id: str, *, worker: bool = False) -> MemoryUnitOfWork:
        # A worker UoW sees all tenants: emulate by using a wildcard visibility.
        if worker:
            return _WorkerUoW(state)
        return MemoryUnitOfWork(state, tenant_id)

    return factory


class _WorkerUoW(MemoryUnitOfWork):
    """Cross-tenant worker view for the scheduler/reaper in memory mode."""

    def __init__(self, state: MemoryState):
        super().__init__(state, tenant_id="*")

    async def __aenter__(self):
        await super().__aenter__()

        # Override visibility to see all tenants.
        def _all(_obj):
            return True

        self.jobs._visible = _all  # type: ignore[method-assign]
        self.schedules._visible = _all  # type: ignore[method-assign]
        return self
