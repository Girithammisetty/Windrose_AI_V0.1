"""Ports (interfaces) between the domain and adapters/stores."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from app.domain.entities import Dataset, DatasetVersion, LineageEdge, Profile

# ---------------------------------------------------------------------------
# Repositories


@dataclass(slots=True)
class Page:
    items: list[Any]
    next_cursor: str | None
    has_more: bool


@dataclass(slots=True)
class DatasetFilters:
    q: str | None = None
    status: str | None = None
    tags: list[str] = field(default_factory=list)
    created_by: str | None = None
    column: str | None = None
    quality_flag: str | None = None
    has_pii: bool | None = None
    include_deleted: bool = False
    ids: list[str] | None = None  # pre-ranked ids from the SearchIndex (q path)


class DatasetRepo(Protocol):
    async def add(self, dataset: Dataset) -> None: ...
    async def get(self, dataset_id: str, include_deleted: bool = False) -> Dataset | None: ...
    async def get_by_name(self, workspace_id: str, name: str) -> Dataset | None: ...
    async def get_by_name_in_tenant(self, name: str) -> Dataset | None:
        """Resolve a dataset by name within the UoW's tenant (any workspace).

        The RLS tenant GUC already scopes the query, so no workspace is needed —
        used by the internal /resolve endpoint which only knows (tenant, name)."""
        ...
    async def update(self, dataset: Dataset) -> None: ...
    async def list(
        self, filters: DatasetFilters, sort: str, limit: int, cursor: str | None
    ) -> Page: ...
    async def all_active(self) -> list[Dataset]: ...
    async def soft_deleted_before(self, cutoff: datetime) -> list[Dataset]: ...
    async def hard_delete(self, dataset_id: str) -> None: ...


class VersionRepo(Protocol):
    async def add(self, version: DatasetVersion) -> None: ...
    async def get(self, dataset_id: str, version_no: int) -> DatasetVersion | None: ...
    async def get_by_id(self, version_id: str) -> DatasetVersion | None: ...
    async def latest(self, dataset_id: str) -> DatasetVersion | None: ...
    async def list(self, dataset_id: str, limit: int, cursor: str | None) -> Page: ...
    async def list_all(self, dataset_id: str) -> list[DatasetVersion]: ...
    async def by_snapshot(self, dataset_id: str, snapshot_id: int) -> DatasetVersion | None: ...
    async def by_produced_by(self, produced_by_urn: str) -> DatasetVersion | None: ...
    async def next_version_no(self, dataset_id: str) -> int:
        """Assign the next version_no under a per-dataset lock (BR-2)."""
        ...

    async def update(self, version: DatasetVersion) -> None: ...


class ProfileRepo(Protocol):
    async def add(self, profile: Profile) -> None: ...
    async def get(self, profile_id: str) -> Profile | None: ...
    async def update(self, profile: Profile) -> None: ...
    async def count_since(self, dataset_id: str, since: datetime) -> int: ...
    async def running_started_before(self, cutoff: datetime) -> list[Profile]: ...


class LineageRepo(Protocol):
    async def upsert(self, edge: LineageEdge) -> tuple[LineageEdge, bool]:
        """Idempotent on (tenant, from, to, activity, run_urn); returns (edge, created)."""
        ...

    async def edges_touching(
        self, urns: set[str], direction: str, activities: list[str] | None
    ) -> list[LineageEdge]: ...
    async def edges_from(self, urns: set[str]) -> list[LineageEdge]: ...
    async def trained_edges_since(self, since: datetime) -> list[LineageEdge]: ...


class OutboxRepo(Protocol):
    async def add(self, topic: str, envelope: dict) -> None: ...


class IdempotencyRepo(Protocol):
    async def get(self, key: str) -> dict | None: ...
    async def put(self, key: str, request_hash: str, status_code: int, body: dict) -> None: ...


class UnitOfWork(Protocol):
    """Tenant-scoped unit of work. SQL impl binds RLS via `app.tenant_id`."""

    tenant_id: str
    datasets: DatasetRepo
    versions: VersionRepo
    profiles: ProfileRepo
    lineage: LineageRepo
    outbox: OutboxRepo
    idempotency: IdempotencyRepo

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UowFactory(Protocol):
    def __call__(self, tenant_id: str) -> UnitOfWork: ...


# ---------------------------------------------------------------------------
# Adapters


class Catalog(Protocol):
    """Iceberg catalog abstraction (DST-FR-003/004, BR-1)."""

    async def snapshot_exists(self, table: str, snapshot_id: int) -> bool: ...
    async def read_snapshot(self, table: str, snapshot_id: int) -> pd.DataFrame: ...
    async def expire_snapshot(self, table: str, snapshot_id: int) -> None: ...
    async def drop_table(self, table: str) -> None: ...
    async def data_file_uris(
        self, table: str, snapshot_id: int | None = None
    ) -> list[str]:
        """Physical parquet data files for a pinned snapshot (QRY-FR-005)."""
        ...

    async def table_columns(self, table: str) -> list[dict[str, str]]:
        """Columns from the physical table schema (name/type)."""
        ...


class ObjectStore(Protocol):
    """Profile blob storage (DST-FR-022, MASTER-FR-061)."""

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def signed_url(self, key: str, ttl_hours: int) -> str: ...


@dataclass(slots=True)
class ProfileJobSpec:
    tenant_id: str
    dataset_id: str
    dataset_urn: str
    version_no: int
    profile_id: str
    iceberg_table: str
    iceberg_snapshot_id: int
    sample_strategy: str
    callback_token: str
    output_prefix: str


class ProfilerRunner(Protocol):
    """Launches a profiler job (containerized in prod; in-process fake in dev/tests)."""

    async def launch(self, spec: ProfileJobSpec) -> None: ...
    async def kill(self, profile_id: str) -> None: ...


class SearchIndex(Protocol):
    """Catalog full-text search (DST-FR-060). PG FTS now; OpenSearch later."""

    async def index_dataset(self, dataset: Dataset) -> None: ...
    async def remove_dataset(self, tenant_id: str, dataset_id: str) -> None: ...
    async def search(self, tenant_id: str, q: str, limit: int = 500) -> list[str]:
        """Ranked dataset ids matching q for this tenant."""
        ...


class EventBus(Protocol):
    async def publish(self, topic: str, envelope: dict) -> None: ...


class DedupStore(Protocol):
    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        """Read-only check: True if this event was already fully handled."""
        ...

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        """Record the event as processed. Called only AFTER handler effects are
        durable so a mid-handler failure leaves the event un-deduped for
        idempotent redelivery (exactly-once effect; MASTER-FR-032)."""
        ...
