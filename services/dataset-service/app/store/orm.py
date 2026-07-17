"""SQLAlchemy ORM models (BRD §4.1).

Deviation note: dataset_versions / lineage_edges are specced as monthly
partitioned; native partitioning is deferred (TODO in migration) because it
forces the partition key into every unique constraint. Retention windows are
enforced by the retention job instead.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DatasetRow(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="workspace")
    lifecycle: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    successor_urn: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    error_log: Mapped[dict | None] = mapped_column(JSONB)
    iceberg_table: Mapped[str] = mapped_column(Text, nullable=False)
    partition_spec: Mapped[dict | None] = mapped_column(JSONB)
    current_version_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    custom_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetVersionRow(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version_no"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    iceberg_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema: Mapped[dict] = mapped_column("schema", JSONB, nullable=False)
    schema_diff: Mapped[dict | None] = mapped_column(JSONB)
    breaking_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    produced_by_urn: Mapped[str | None] = mapped_column(Text)
    profile_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    profile_status: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProfileRow(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    error_category: Mapped[str | None] = mapped_column(Text)
    object_key_json: Mapped[str | None] = mapped_column(Text)
    object_key_html: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    sample: Mapped[dict | None] = mapped_column(JSONB)
    profiler_version: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    callback_token: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LineageEdgeRow(Base):
    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint("tenant_id", "from_urn", "to_urn", "activity", "run_urn"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    from_urn: Mapped[str] = mapped_column(Text, nullable=False)
    to_urn: Mapped[str] = mapped_column(Text, nullable=False)
    activity: Mapped[str] = mapped_column(Text, nullable=False)
    run_urn: Mapped[str | None] = mapped_column(Text)
    properties: Mapped[dict | None] = mapped_column(JSONB)
    actor: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
