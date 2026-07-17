"""SQLAlchemy ORM row models mapped to the migration schema.

UUID columns use ``UUID(as_uuid=False)`` so the asyncpg driver binds our UUIDv7
string ids as real ``uuid`` values (Postgres will not implicitly cast text).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Uid = UUID(as_uuid=False)


class Base(DeclarativeBase):
    pass


class InferenceJobRow(Base):
    __tablename__ = "inference_jobs"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Uid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    model_version_urn: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[int | None] = mapped_column(Integer)
    model_stage_at_submit: Mapped[int | None] = mapped_column(SmallInteger)
    input_dataset_urn: Mapped[str] = mapped_column(Text, nullable=False)
    input_dataset_version: Mapped[int | None] = mapped_column(Integer)
    output_dataset_urn: Mapped[str | None] = mapped_column(Text)
    output_dataset_version: Mapped[int | None] = mapped_column(Integer)
    output_mode: Mapped[int] = mapped_column(SmallInteger, default=0)
    output_dataset_name: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict | None] = mapped_column(JSONB)
    compatibility_report: Mapped[dict | None] = mapped_column(JSONB)
    pipeline_run_urn: Mapped[str | None] = mapped_column(Text)
    components_status: Mapped[list | None] = mapped_column(JSONB)
    error: Mapped[dict | None] = mapped_column(JSONB)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    schedule_id: Mapped[str | None] = mapped_column(Uid)
    retried_from_job_id: Mapped[str | None] = mapped_column(Uid)
    submitted_by: Mapped[str] = mapped_column(Text, nullable=False)
    via_agent: Mapped[dict | None] = mapped_column(JSONB)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScoringScheduleRow(Base):
    __tablename__ = "scoring_schedules"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Uid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model_version_urn: Mapped[str | None] = mapped_column(Text)
    model_urn: Mapped[str | None] = mapped_column(Text)
    stage_selector: Mapped[int | None] = mapped_column(SmallInteger)
    input_selector: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cron: Mapped[str | None] = mapped_column(Text)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(Text, default="UTC")
    overlap_policy: Mapped[int] = mapped_column(SmallInteger, default=0)
    output: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_reason: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    temporal_schedule_id: Mapped[str | None] = mapped_column(Text)
    notify_on_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobQueueRow(Base):
    __tablename__ = "job_queue"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    job_id: Mapped[str] = mapped_column(Uid, nullable=False, unique=True)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InputDatasetRow(Base):
    """Local resolution table for input datasets (real Postgres). Stands in for a
    dataset-service read: carries the current-version schema + the real parquet
    location in MinIO so validation and scoring resolve against real data."""

    __tablename__ = "input_datasets"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    urn: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutputDatasetRow(Base):
    __tablename__ = "output_datasets"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Uid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    urn: Mapped[str] = mapped_column(Text, nullable=False)
    owner_model_urn: Mapped[str] = mapped_column(Text, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutputDatasetVersionRow(Base):
    __tablename__ = "output_dataset_versions"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    dataset_id: Mapped[str] = mapped_column(Uid, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_id: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    produced_by_job_id: Mapped[str | None] = mapped_column(Uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LineageEdgeRow(Base):
    __tablename__ = "lineage_edges"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    from_urn: Mapped[str] = mapped_column(Text, nullable=False)
    to_urn: Mapped[str] = mapped_column(Text, nullable=False)
    activity: Mapped[str] = mapped_column(Text, nullable=False)
    run_urn: Mapped[str | None] = mapped_column(Text)
    properties: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServingEndpointRow(Base):
    """Reserved (INF-FR-070). No writes this phase."""

    __tablename__ = "serving_endpoints"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Uid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model_version_urn: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    kserve_ref: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxRow(Base):
    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    tenant_id: Mapped[str] = mapped_column(Uid, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessedEventRow(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(Uid, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Uid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
