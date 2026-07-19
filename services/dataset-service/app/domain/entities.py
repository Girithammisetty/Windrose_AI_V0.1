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


# ---- BRD 56 inc2: persisted entity resolution -----------------------------


class MergeCandidateStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(slots=True)
class EntityResolutionConfig:
    """ER-FR-001: a tenant-scoped, VERSIONED resolution config. Re-resolving a
    dataset under changed rules mints a new version so every run is attributable
    to the exact rules that produced it (BR-5)."""
    id: str
    tenant_id: str
    dataset_id: str
    entity_type: str
    version_no: int
    deterministic_keys: list[list[str]]
    scoring_fields: list[dict]
    blocking_fields: list[str]
    auto_merge_threshold: float
    review_threshold: float
    pk_column: str
    created_by: str
    created_at: datetime


@dataclass(slots=True)
class EntityResolutionRun:
    """ER-FR-010/040: one execution of a config version over a dataset's rows,
    with the aggregate counts the run produced (audit + reproducibility)."""
    id: str
    tenant_id: str
    dataset_id: str
    config_id: str
    entity_type: str
    record_count: int
    resolved_entity_count: int
    merged_cluster_count: int
    review_candidate_count: int
    status: str
    created_by: str
    created_at: datetime


@dataclass(slots=True)
class ResolvedEntity:
    """ER-FR-010: a stable resolved cluster produced by a run."""
    resolved_entity_id: str
    run_id: str
    tenant_id: str
    dataset_id: str
    entity_type: str
    member_count: int
    confidence: float
    method: str


@dataclass(slots=True)
class ResolvedEntityMember:
    """ER-FR-040: lineage — which source record joined which cluster, on what
    matching evidence (reconstructable + defensible under exam)."""
    id: str
    resolved_entity_id: str
    run_id: str
    tenant_id: str
    member_pk: str
    method: str
    evidence: list[dict]


@dataclass(slots=True)
class EntityMergeCandidate:
    """ER-FR-030: a below-auto, above-review probable merge PROPOSED for a
    steward's four-eyes review — never silently merged (BR-1)."""
    id: str
    run_id: str
    tenant_id: str
    dataset_id: str
    entity_type: str
    left_pk: str
    right_pk: str
    score: float
    evidence: dict
    status: str
    proposal_id: str | None
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


@dataclass(slots=True)
class OntologyEntity:
    """A governed domain ONTOLOGY entity type (inc11): a named type the vertical
    operates on (Vendor, Invoice, PaymentRun, ...) with its attributes and typed
    RELATIONSHIPS to other types. The type-level domain model — distinct from the
    dataset-derived semantic entities (flat, no relationships) and from entity
    RESOLUTION (which resolves instances of these types)."""

    id: str
    tenant_id: str
    workspace_id: str
    entity_key: str
    name: str
    description: str = ""
    # attributes: [{name, data_type, description?}]
    attributes: list = field(default_factory=list)
    # relationships: [{name, target, cardinality}] e.g. {name: invoices, target:
    # invoice, cardinality: has_many}
    relationships: list = field(default_factory=list)
    created_by: str = "unknown"
    created_at: datetime | None = None
    updated_at: datetime | None = None
