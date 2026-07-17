"""Domain entities & enums (BRD §4.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DatasetStatus(StrEnum):
    DRAFT = "draft"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Lifecycle(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class Visibility(StrEnum):
    WORKSPACE = "workspace"
    TENANT_PUBLIC = "tenant_public"


class ProfileStatus(StrEnum):
    NONE = "none"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ProfileErrorCategory(StrEnum):
    EMPTY_DATA = "EMPTY_DATA"
    UNNAMED_COLUMNS = "UNNAMED_COLUMNS"
    SAMPLING_FAILED = "SAMPLING_FAILED"
    OOM = "OOM"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"


class Activity(StrEnum):
    INGESTED = "ingested"
    TRANSFORMED = "transformed"
    TRAINED = "trained"
    INFERRED = "inferred"
    EXPORTED = "exported"
    DERIVED = "derived"


@dataclass(slots=True)
class Dataset:
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    iceberg_table: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    visibility: str = Visibility.WORKSPACE
    lifecycle: str = Lifecycle.ACTIVE
    successor_urn: str | None = None
    status: str = DatasetStatus.DRAFT
    error_log: dict | None = None
    partition_spec: dict | None = None
    current_version_id: str | None = None
    tags: list[str] = field(default_factory=list)
    custom_metadata: dict | None = None
    deleted_at: datetime | None = None


@dataclass(slots=True)
class DatasetVersion:
    id: str
    tenant_id: str
    dataset_id: str
    version_no: int
    iceberg_snapshot_id: int
    schema: dict  # column name -> {type, nullable, tags[]}
    created_at: datetime
    schema_diff: dict | None = None
    breaking_change: bool = False
    row_count: int | None = None
    bytes: int | None = None
    produced_by_urn: str | None = None
    profile_id: str | None = None
    profile_status: str = ProfileStatus.NONE
    expired: bool = False


@dataclass(slots=True)
class Profile:
    id: str
    tenant_id: str
    dataset_id: str
    version_id: str
    created_at: datetime
    status: str = ProfileStatus.PENDING
    error_category: str | None = None
    object_key_json: str | None = None
    object_key_html: str | None = None
    summary: dict | None = None
    sample: dict | None = None
    profiler_version: str | None = None
    attempt: int = 1
    callback_token: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class LineageEdge:
    id: str
    tenant_id: str
    from_urn: str
    to_urn: str
    activity: str
    occurred_at: datetime
    created_at: datetime
    run_urn: str | None = None
    properties: dict | None = None
    actor: dict | None = None
