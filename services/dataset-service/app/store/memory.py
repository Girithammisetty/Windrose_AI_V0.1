"""In-memory store: unit-tier implementation of the repository ports.

Acts as the in-memory tenant-policy fake required by CONVENTIONS.md — every
repo method is scoped to the UoW's tenant, so cross-tenant reads return
nothing, mirroring Postgres RLS behavior.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import defaultdict
from datetime import datetime

from app.domain.entities import (
    Dataset,
    DatasetVersion,
    EntityMergeCandidate,
    EntityResolutionConfig,
    EntityResolutionRun,
    LineageEdge,
    OntologyEntity,
    Profile,
    ResolvedEntity,
    ResolvedEntityMember,
)
from app.domain.ports import DatasetFilters, Page
from app.utils import decode_cursor, encode_cursor


class MemoryState:
    """Shared backing store across units of work (one per process/test app)."""

    def __init__(self):
        self.datasets: dict[str, Dataset] = {}
        self.versions: dict[str, DatasetVersion] = {}
        self.profiles: dict[str, Profile] = {}
        self.edges: dict[str, LineageEdge] = {}
        self.outbox: list[tuple[str, dict]] = []
        self.idempotency: dict[tuple[str, str], dict] = {}
        self.version_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # BRD 56 inc2 entity resolution
        self.er_configs: dict[str, EntityResolutionConfig] = {}
        self.er_runs: dict[str, EntityResolutionRun] = {}
        self.resolved_entities: list[ResolvedEntity] = []
        self.resolved_members: list[ResolvedEntityMember] = []
        self.merge_candidates: dict[str, EntityMergeCandidate] = {}
        # inc11: (workspace_id, entity_key) -> ontology entity type
        self.ontology: dict[tuple[str, str], OntologyEntity] = {}

    def events_of_type(self, event_type: str) -> list[dict]:
        return [e for _, e in self.outbox if e["event_type"] == event_type]


def _copy(entity):
    return dataclasses.replace(entity) if entity is not None else None


def _dataset_matches(state: MemoryState, ds: Dataset, f: DatasetFilters) -> bool:
    if f.status and ds.status != f.status:
        return False
    if f.created_by and ds.created_by != f.created_by:
        return False
    if f.tags and not set(t.lower() for t in f.tags) <= {t.lower() for t in ds.tags}:
        return False
    current = state.versions.get(ds.current_version_id) if ds.current_version_id else None
    if f.column:
        cols = {c.lower() for c in (current.schema if current else {})}
        if f.column.lower() not in cols:
            return False
    if f.has_pii is not None:
        schema_tags = [
            t for c in (current.schema.values() if current else [])
            for t in (c.get("tags") or [])
        ]
        pii = any(t.startswith("pii") for t in list(ds.tags) + schema_tags)
        if pii != f.has_pii:
            return False
    if f.quality_flag:
        profile = state.profiles.get(current.profile_id) if current and current.profile_id else None
        flags = {
            flag
            for col in ((profile.summary or {}).get("columns", []) if profile else [])
            for flag in col.get("quality_flags", [])
        }
        if f.quality_flag not in flags:
            return False
    return True


def _paginate(items: list, limit: int, cursor: str | None) -> Page:
    offset = 0
    if cursor:
        offset = int(decode_cursor(cursor).get("o", 0))
    window = items[offset : offset + limit]
    has_more = offset + limit < len(items)
    next_cursor = encode_cursor({"o": offset + limit}) if has_more else None
    return Page(items=window, next_cursor=next_cursor, has_more=has_more)


class MemoryDatasetRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    def _visible(self, ds: Dataset | None, include_deleted: bool = False) -> Dataset | None:
        if ds is None or ds.tenant_id != self.tenant_id:
            return None  # policy fake: cross-tenant rows do not exist
        if ds.deleted_at and not include_deleted:
            return None
        return ds

    async def add(self, dataset: Dataset) -> None:
        self.state.datasets[dataset.id] = _copy(dataset)

    async def get(self, dataset_id: str, include_deleted: bool = False) -> Dataset | None:
        return _copy(self._visible(self.state.datasets.get(dataset_id), include_deleted))

    async def get_by_name(self, workspace_id: str, name: str) -> Dataset | None:
        for ds in self.state.datasets.values():
            if (
                self._visible(ds)
                and ds.workspace_id == workspace_id
                and ds.name.lower() == name.lower()
            ):
                return _copy(ds)
        return None

    async def get_by_name_in_tenant(self, name: str) -> Dataset | None:
        matches = [
            ds for ds in self.state.datasets.values()
            if self._visible(ds) and ds.name.lower() == name.lower()
        ]
        matches.sort(key=lambda ds: ds.created_at, reverse=True)
        return _copy(matches[0]) if matches else None

    async def update(self, dataset: Dataset) -> None:
        current = self.state.datasets.get(dataset.id)
        if current and current.tenant_id == self.tenant_id:
            self.state.datasets[dataset.id] = _copy(dataset)

    async def list(self, filters: DatasetFilters, sort: str, limit: int,
                   cursor: str | None) -> Page:
        rows = [
            ds for ds in self.state.datasets.values()
            if self._visible(ds, filters.include_deleted)
            and _dataset_matches(self.state, ds, filters)
        ]
        if filters.ids is not None:
            rank = {did: i for i, did in enumerate(filters.ids)}
            rows = sorted(
                (ds for ds in rows if ds.id in rank), key=lambda ds: rank[ds.id]
            )
        else:
            reverse = sort.startswith("-")
            key = sort.lstrip("-")
            if key == "name":
                rows.sort(key=lambda ds: ds.name.lower(), reverse=reverse)
            elif key == "row_count":
                def row_count(ds: Dataset) -> int:
                    v = self.state.versions.get(ds.current_version_id or "")
                    return v.row_count or 0 if v else 0
                rows.sort(key=row_count, reverse=reverse)
            else:
                rows.sort(key=lambda ds: (ds.created_at, ds.id), reverse=reverse)
        page = _paginate(rows, limit, cursor)
        page.items = [_copy(ds) for ds in page.items]
        return page

    async def all_active(self) -> list[Dataset]:
        return [_copy(ds) for ds in self.state.datasets.values() if self._visible(ds)]

    async def soft_deleted_before(self, cutoff: datetime) -> list[Dataset]:
        return [
            _copy(ds)
            for ds in self.state.datasets.values()
            if ds.tenant_id == self.tenant_id and ds.deleted_at and ds.deleted_at < cutoff
        ]

    async def hard_delete(self, dataset_id: str) -> None:
        ds = self.state.datasets.get(dataset_id)
        if ds and ds.tenant_id == self.tenant_id:
            del self.state.datasets[dataset_id]
            for vid in [v.id for v in self.state.versions.values()
                        if v.dataset_id == dataset_id]:
                del self.state.versions[vid]
            for pid in [p.id for p in self.state.profiles.values()
                        if p.dataset_id == dataset_id]:
                del self.state.profiles[pid]


class MemoryVersionRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    def _mine(self, v: DatasetVersion | None) -> DatasetVersion | None:
        return v if v is not None and v.tenant_id == self.tenant_id else None

    async def add(self, version: DatasetVersion) -> None:
        self.state.versions[version.id] = _copy(version)

    async def get(self, dataset_id: str, version_no: int) -> DatasetVersion | None:
        for v in self.state.versions.values():
            if self._mine(v) and v.dataset_id == dataset_id and v.version_no == version_no:
                return _copy(v)
        return None

    async def get_by_id(self, version_id: str) -> DatasetVersion | None:
        return _copy(self._mine(self.state.versions.get(version_id)))

    async def latest(self, dataset_id: str) -> DatasetVersion | None:
        mine = [v for v in self.state.versions.values()
                if self._mine(v) and v.dataset_id == dataset_id]
        return _copy(max(mine, key=lambda v: v.version_no)) if mine else None

    async def list(self, dataset_id: str, limit: int, cursor: str | None) -> Page:
        mine = sorted(
            (v for v in self.state.versions.values()
             if self._mine(v) and v.dataset_id == dataset_id),
            key=lambda v: -v.version_no,
        )
        page = _paginate(mine, limit, cursor)
        page.items = [_copy(v) for v in page.items]
        return page

    async def list_all(self, dataset_id: str) -> list[DatasetVersion]:
        return sorted(
            (_copy(v) for v in self.state.versions.values()
             if self._mine(v) and v.dataset_id == dataset_id),
            key=lambda v: v.version_no,
        )

    async def by_snapshot(self, dataset_id: str, snapshot_id: int) -> DatasetVersion | None:
        for v in self.state.versions.values():
            if (self._mine(v) and v.dataset_id == dataset_id
                    and v.iceberg_snapshot_id == snapshot_id):
                return _copy(v)
        return None

    async def by_produced_by(self, produced_by_urn: str) -> DatasetVersion | None:
        for v in self.state.versions.values():
            if self._mine(v) and v.produced_by_urn == produced_by_urn:
                return _copy(v)
        return None

    async def next_version_no(self, dataset_id: str) -> int:
        async with self.state.version_locks[dataset_id]:
            latest = await self.latest(dataset_id)
            return (latest.version_no if latest else 0) + 1

    async def update(self, version: DatasetVersion) -> None:
        current = self.state.versions.get(version.id)
        if current and current.tenant_id == self.tenant_id:
            self.state.versions[version.id] = _copy(version)


class MemoryProfileRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    async def add(self, profile: Profile) -> None:
        self.state.profiles[profile.id] = _copy(profile)

    async def get(self, profile_id: str) -> Profile | None:
        p = self.state.profiles.get(profile_id)
        return _copy(p) if p and p.tenant_id == self.tenant_id else None

    async def update(self, profile: Profile) -> None:
        current = self.state.profiles.get(profile.id)
        if current and current.tenant_id == self.tenant_id:
            self.state.profiles[profile.id] = _copy(profile)

    async def count_since(self, dataset_id: str, since: datetime) -> int:
        return sum(
            1 for p in self.state.profiles.values()
            if p.tenant_id == self.tenant_id and p.dataset_id == dataset_id
            and p.created_at >= since
        )

    async def running_started_before(self, cutoff: datetime) -> list[Profile]:
        out = []
        for p in self.state.profiles.values():
            if p.tenant_id != self.tenant_id or p.status not in ("pending", "running"):
                continue
            anchor = p.started_at or p.created_at
            if anchor < cutoff:
                out.append(_copy(p))
        return out


class MemoryLineageRepo:
    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    def _mine(self) -> list[LineageEdge]:
        return [e for e in self.state.edges.values() if e.tenant_id == self.tenant_id]

    async def upsert(self, edge: LineageEdge) -> tuple[LineageEdge, bool]:
        for existing in self._mine():
            if (existing.from_urn, existing.to_urn, existing.activity, existing.run_urn) == (
                edge.from_urn, edge.to_urn, edge.activity, edge.run_urn
            ):
                return _copy(existing), False
        self.state.edges[edge.id] = _copy(edge)
        return _copy(edge), True

    async def edges_touching(
        self, urns: set[str], direction: str, activities: list[str] | None
    ) -> list[LineageEdge]:
        out = []
        for e in self._mine():
            if activities and e.activity not in activities:
                continue
            downstream_hit = direction in ("downstream", "both") and e.from_urn in urns
            upstream_hit = direction in ("upstream", "both") and e.to_urn in urns
            if downstream_hit or upstream_hit:
                out.append(_copy(e))
        return out

    async def edges_from(self, urns: set[str]) -> list[LineageEdge]:
        return [_copy(e) for e in self._mine() if e.from_urn in urns]

    async def trained_edges_since(self, since: datetime) -> list[LineageEdge]:
        return [
            _copy(e) for e in self._mine()
            if e.activity == "trained" and e.occurred_at >= since
        ]


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


class MemoryEntityResolutionRepo:
    """In-memory mirror of SqlEntityResolutionRepo for the unit/dev tier."""

    def __init__(self, state: MemoryState, tenant_id: str):
        self.state, self.tenant_id = state, tenant_id

    async def next_config_version(self, dataset_id: str, entity_type: str) -> int:
        vs = [c.version_no for c in self.state.er_configs.values()
              if c.tenant_id == self.tenant_id and c.dataset_id == dataset_id
              and c.entity_type == entity_type]
        return (max(vs) + 1) if vs else 1

    async def add_config(self, cfg: EntityResolutionConfig) -> None:
        self.state.er_configs[cfg.id] = _copy(cfg)

    async def get_config(self, config_id: str) -> EntityResolutionConfig | None:
        c = self.state.er_configs.get(config_id)
        return _copy(c) if c and c.tenant_id == self.tenant_id else None

    async def add_run(self, run: EntityResolutionRun) -> None:
        self.state.er_runs[run.id] = _copy(run)

    async def get_run(self, run_id: str) -> EntityResolutionRun | None:
        r = self.state.er_runs.get(run_id)
        return _copy(r) if r and r.tenant_id == self.tenant_id else None

    async def list_runs(self, dataset_id: str, limit: int = 50) -> list[EntityResolutionRun]:
        rs = [r for r in self.state.er_runs.values()
              if r.tenant_id == self.tenant_id and r.dataset_id == dataset_id]
        rs.sort(key=lambda r: r.created_at, reverse=True)
        return [_copy(r) for r in rs[:limit]]

    async def add_resolved_entities(self, items: list[ResolvedEntity]) -> None:
        self.state.resolved_entities.extend(_copy(e) for e in items)

    async def add_members(self, items: list[ResolvedEntityMember]) -> None:
        self.state.resolved_members.extend(_copy(m) for m in items)

    async def add_candidates(self, items: list[EntityMergeCandidate]) -> None:
        for c in items:
            self.state.merge_candidates[c.id] = _copy(c)

    async def list_resolved_entities(self, run_id: str) -> list[ResolvedEntity]:
        return sorted(
            [_copy(e) for e in self.state.resolved_entities
             if e.tenant_id == self.tenant_id and e.run_id == run_id],
            key=lambda e: e.resolved_entity_id)

    async def list_members(self, run_id: str) -> list[ResolvedEntityMember]:
        return [_copy(m) for m in self.state.resolved_members
                if m.tenant_id == self.tenant_id and m.run_id == run_id]

    async def list_candidates(self, run_id: str,
                              status: str | None = None) -> list[EntityMergeCandidate]:
        cs = [c for c in self.state.merge_candidates.values()
              if c.tenant_id == self.tenant_id and c.run_id == run_id
              and (status is None or c.status == status)]
        cs.sort(key=lambda c: c.score, reverse=True)
        return [_copy(c) for c in cs]

    async def get_candidate(self, candidate_id: str) -> EntityMergeCandidate | None:
        c = self.state.merge_candidates.get(candidate_id)
        return _copy(c) if c and c.tenant_id == self.tenant_id else None

    async def set_candidate_proposal(self, candidate_id: str, proposal_id: str) -> None:
        c = self.state.merge_candidates.get(candidate_id)
        if c and c.tenant_id == self.tenant_id:
            c.proposal_id = proposal_id

    async def decide_candidate(self, candidate_id: str, *, status: str,
                               decided_by: str, decided_at: datetime) -> None:
        c = self.state.merge_candidates.get(candidate_id)
        if c and c.tenant_id == self.tenant_id:
            c.status, c.decided_by, c.decided_at = status, decided_by, decided_at


class MemoryUnitOfWork:
    """Mutations apply immediately (unit-tier simplification); outbox entries are
    staged and flushed on commit so tests still observe emit-after-commit order."""

    def __init__(self, state: MemoryState, tenant_id: str):
        self.tenant_id = tenant_id
        self._staged_outbox: list[tuple[str, dict]] = []
        self.datasets = MemoryDatasetRepo(state, tenant_id)
        self.versions = MemoryVersionRepo(state, tenant_id)
        self.profiles = MemoryProfileRepo(state, tenant_id)
        self.lineage = MemoryLineageRepo(state, tenant_id)
        self.outbox = MemoryOutboxRepo(state, self._staged_outbox)
        self.idempotency = MemoryIdempotencyRepo(state, tenant_id)
        self.entity_resolution = MemoryEntityResolutionRepo(state, tenant_id)
        self.ontology = MemoryOntologyRepo(state, tenant_id)
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            await self.commit()

    async def commit(self):
        self._state.outbox.extend(self._staged_outbox)
        self._staged_outbox.clear()

    async def rollback(self):
        self._staged_outbox.clear()


def memory_uow_factory(state: MemoryState):
    def factory(tenant_id: str) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(state, tenant_id)

    return factory


class MemoryOntologyRepo:
    """In-memory mirror of SqlOntologyRepo (inc11) for the unit/dev tier."""

    def __init__(self, state: MemoryState, tenant_id: str):
        self.st = state
        self.t = tenant_id

    async def get(self, workspace_id, entity_key):
        e = self.st.ontology.get((workspace_id, entity_key))
        return _copy(e) if e and e.tenant_id == self.t else None

    async def list(self, workspace_id):
        out = [_copy(e) for e in self.st.ontology.values()
               if e.tenant_id == self.t and (not workspace_id or e.workspace_id == workspace_id)]
        return sorted(out, key=lambda e: e.entity_key)

    async def add(self, e: OntologyEntity):
        self.st.ontology[(e.workspace_id, e.entity_key)] = _copy(e)

    async def delete(self, workspace_id, entity_key):
        return self.st.ontology.pop((workspace_id, entity_key), None) is not None
