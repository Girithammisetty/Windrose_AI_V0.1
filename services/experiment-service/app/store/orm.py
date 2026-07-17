"""SQLAlchemy ORM models (BRD §4.1).

Deviation note: run_metric_history is specced as monthly-partitioned; native
partitioning is deferred (documented in README) because the partition key would
have to join every unique constraint — the retention job enforces the 12-month
hot window instead. This mirrors the accepted dataset-service precedent.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ExperimentRow(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    model_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mlflow_experiment_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_pipeline_urn: Mapped[str] = mapped_column(Text, nullable=False)
    feature_engineering_pipeline_urn: Mapped[str] = mapped_column(Text, nullable=False)
    training_pipeline_urn: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    experiment_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    mlflow_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    algorithm: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    pipeline_run_urn: Mapped[str | None] = mapped_column(Text)
    input_dataset_urns: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    output_dataset_urns: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    error_messages: Mapped[dict | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunParamRow(Base):
    __tablename__ = "run_params"

    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    param_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RunMetricRow(Base):
    __tablename__ = "run_metrics"

    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunMetricHistoryRow(Base):
    __tablename__ = "run_metric_history"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunTagRow(Base):
    __tablename__ = "run_tags"

    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class RunArtifactRow(Base):
    __tablename__ = "run_artifacts"

    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    content_type: Mapped[str | None] = mapped_column(Text)


class RunNoteRow(Base):
    __tablename__ = "run_notes"

    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RegisteredModelRow(Base):
    __tablename__ = "registered_models"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelVersionRow(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    mlflow_model_ref: Mapped[str | None] = mapped_column(Text)
    flavor: Mapped[str] = mapped_column(Text, nullable=False, default="mlflow.sklearn")
    input_schema: Mapped[dict | None] = mapped_column(JSONB)
    output_schema: Mapped[dict | None] = mapped_column(JSONB)
    stage: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    stage_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromotionRow(Base):
    __tablename__ = "promotions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    target_stage: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    from_stage: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    rationale: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    via_agent: Mapped[dict | None] = mapped_column(JSONB)
    workflow_id: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[dict | None] = mapped_column(JSONB)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelRegistrationLogRow(Base):
    __tablename__ = "model_registration_log"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    experiment_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    run_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    registered_by: Mapped[str] = mapped_column(Text, nullable=False)
    via_agent: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelCardRow(Base):
    __tablename__ = "model_cards"

    model_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    auto_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    overlay: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    overlay_updated_by: Mapped[str | None] = mapped_column(Text)
    overlay_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MirrorInboxRow(Base):
    __tablename__ = "mirror_inbox"

    delivery_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class ReconciliationWatermarkRow(Base):
    __tablename__ = "reconciliation_watermarks"

    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    mlflow_experiment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxRow(Base):
    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessedEventRow(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
