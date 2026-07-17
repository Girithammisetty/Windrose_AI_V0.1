"""Domain entities (BRD §4.1). Plain dataclasses; the store maps them to rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import JobStatus


@dataclass
class InferenceJob:
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    status: int  # JobStatus
    model_version_urn: str
    input_dataset_urn: str
    submitted_by: str
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    model_name: str | None = None
    model_version: int | None = None
    model_stage_at_submit: int | None = None
    input_dataset_version: int | None = None
    output_dataset_urn: str | None = None
    output_dataset_version: int | None = None
    output_mode: int = 0
    output_dataset_name: str | None = None
    parameters: dict = field(default_factory=dict)
    compatibility_report: dict | None = None
    pipeline_run_urn: str | None = None
    components_status: list | None = None
    error: dict | None = None
    row_count: int | None = None
    schedule_id: str | None = None
    retried_from_job_id: str | None = None
    via_agent: dict | None = None
    queued_at: datetime | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        from app.domain.enums import TERMINAL

        return JobStatus(self.status) in TERMINAL


@dataclass
class ScoringSchedule:
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    input_selector: dict
    output: dict
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_version_urn: str | None = None
    model_urn: str | None = None
    stage_selector: int | None = None
    cron: str | None = None
    interval_seconds: int | None = None
    timezone: str = "UTC"
    overlap_policy: int = 0
    enabled: bool = True
    paused_reason: str | None = None
    consecutive_failures: int = 0
    temporal_schedule_id: str | None = None
    notify_on_failure: bool = True
    last_fired_at: datetime | None = None
    next_fire_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass
class LineageEdge:
    id: str
    tenant_id: str
    from_urn: str
    to_urn: str
    activity: str  # used_by | input_to | produced
    occurred_at: datetime
    created_at: datetime
    run_urn: str | None = None
    properties: dict | None = None


@dataclass
class OutputDataset:
    """Local (inference-owned) registry row for a scoring output dataset.

    In production this lives in dataset-service; locally it is a real row backed
    by real parquet in MinIO (the object-store dataset gateway)."""

    id: str
    tenant_id: str
    workspace_id: str
    name: str
    urn: str
    owner_model_urn: str
    current_version: int
    created_at: datetime
    updated_at: datetime
