"""SQLAlchemy 2.0 ORM models (BRD §4.1). Columns mirror the domain entities so the
repos round-trip field-by-field. UUID DB columns use ``UUID(as_uuid=False)`` so string
ids bind/compare correctly under asyncpg; user-sub columns (created_by / submitted_by)
stay text since subjects need not be UUIDs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Integer,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid(**kw):
    return mapped_column(UUID(as_uuid=False), **kw)


class Base(DeclarativeBase):
    pass


class TemplateRow(Base):
    __tablename__ = "pipeline_templates"
    id: Mapped[str] = _uuid(primary_key=True)
    tenant_id: Mapped[str] = _uuid(nullable=False)
    workspace_id: Mapped[str] = _uuid(nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    model_type: Mapped[int | None] = mapped_column(SmallInteger)
    algorithm_template_name: Mapped[str | None] = mapped_column(Text)
    active_version_id: Mapped[str | None] = _uuid()
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VersionRow(Base):
    __tablename__ = "pipeline_template_versions"
    id: Mapped[str] = _uuid(primary_key=True)
    tenant_id: Mapped[str] = _uuid(nullable=False)
    template_id: Mapped[str] = _uuid(nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation_status: Mapped[int] = mapped_column(SmallInteger, default=0)
    validation_report: Mapped[dict | None] = mapped_column(JSONB)
    run_parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    global_parameters: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    component_catalog_version: Mapped[str | None] = mapped_column(Text)
    compiled_manifest_ref: Mapped[str | None] = mapped_column(Text)
    manifest_digest: Mapped[str | None] = mapped_column(Text)
    argo_template_name: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunRow(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[str] = _uuid(primary_key=True)
    tenant_id: Mapped[str] = _uuid(nullable=False)
    template_id: Mapped[str] = _uuid(nullable=False)
    version_id: Mapped[str] = _uuid(nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    argo_workflow_name: Mapped[str | None] = mapped_column(Text)
    mlflow_run_id: Mapped[str | None] = mapped_column(Text)
    run_parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    components_status: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
    input_dataset_urns: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    output_dataset_urns: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    retried_from_run_id: Mapped[str | None] = _uuid()
    submitted_by: Mapped[str | None] = mapped_column(Text)
    via_agent: Mapped[dict | None] = mapped_column(JSONB)
    model_uri: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ComponentRow(Base):
    __tablename__ = "components"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    component_type: Mapped[int] = mapped_column(SmallInteger)
    internal_component_type: Mapped[int] = mapped_column(SmallInteger, default=0)
    label: Mapped[str] = mapped_column(Text)
    definition: Mapped[dict] = mapped_column(JSONB)
    yaml_ref: Mapped[str | None] = mapped_column(Text)
    image_digest: Mapped[str | None] = mapped_column(Text)
    catalog_version: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AlgorithmTemplateRow(Base):
    __tablename__ = "algorithm_templates"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    model_type: Mapped[int] = mapped_column(SmallInteger)
    order_no: Mapped[int] = mapped_column(Integer)
    model_type_order: Mapped[int] = mapped_column(Integer)
    input_type: Mapped[dict] = mapped_column(JSONB)
    pipeline: Mapped[dict] = mapped_column(JSONB)
    tuning_pipeline: Mapped[dict] = mapped_column(JSONB)
    tuning_pipeline_cross_validation: Mapped[dict] = mapped_column(JSONB)
    parameters: Mapped[dict] = mapped_column(JSONB)
    tuning_parameters: Mapped[dict] = mapped_column(JSONB)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB)
    catalog_version: Mapped[str] = mapped_column(Text)
    runnable: Mapped[bool] = mapped_column(Boolean, default=True)


class QuotaRow(Base):
    __tablename__ = "tenant_quotas"
    tenant_id: Mapped[str] = _uuid(primary_key=True)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, default=10)
    max_concurrent_pods: Mapped[int] = mapped_column(Integer, default=40)
    max_run_duration_minutes: Mapped[int] = mapped_column(Integer, default=480)
    min_seconds_between_runs: Mapped[int] = mapped_column(Integer, default=15)
    resource_ceiling: Mapped[dict] = mapped_column(JSONB, default=dict)
    node_pool: Mapped[str | None] = mapped_column(Text)


class RunQueueRow(Base):
    __tablename__ = "run_queue"
    run_id: Mapped[str] = _uuid(primary_key=True)
    tenant_id: Mapped[str] = _uuid(nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LabeledExampleRow(Base):
    __tablename__ = "labeled_examples"
    id: Mapped[str] = _uuid(primary_key=True)
    tenant_id: Mapped[str] = _uuid(nullable=False)
    dataset_urn: Mapped[str] = mapped_column(Text, nullable=False)
    row_pk: Mapped[str] = mapped_column(Text, nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    source_case_urn: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PipelineScheduleRow(Base):
    __tablename__ = "pipeline_schedules"
    schedule_id: Mapped[str] = _uuid(primary_key=True)
    tenant_id: Mapped[str] = _uuid(nullable=False)
    template_id: Mapped[str] = _uuid(nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    cron: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    run_parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[str | None] = _uuid()
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxRow(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = _uuid(primary_key=True)
    tenant_id: Mapped[str] = _uuid(nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"
    tenant_id: Mapped[str] = _uuid(primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProcessedEventRow(Base):
    __tablename__ = "processed_events"
    event_id: Mapped[str] = _uuid(primary_key=True)
    tenant_id: Mapped[str] = _uuid(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
