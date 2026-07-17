"""SQLAlchemy models (BRD 03 §4.1; MASTER-FR-060..063).

Portable across SQLite (unit tier) and Postgres (integration/prod). The
Postgres DDL of record lives in migrations/versions/0001_initial.py, which
additionally applies RLS policies and monthly partitioning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.ids import uuid7

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
UUIDType = sa.Uuid(as_uuid=False)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class Connection(TimestampMixin, Base):
    __tablename__ = "connections"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    workspace_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    connector_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    vault_ref: Mapped[str | None] = mapped_column(sa.Text)
    secret_field_names: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    traffic_direction: Mapped[str] = mapped_column(sa.Text, nullable=False, default="incoming")
    tags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    last_test_status: Mapped[str | None] = mapped_column(sa.Text)
    last_tested_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(sa.Text)  # JWT sub; not necessarily a UUID
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index(
            "uq_connections_tenant_ws_name",
            "tenant_id",
            "workspace_id",
            sa.func.lower(sa.text("name")),
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
            sqlite_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index("ix_connections_tenant_type", "tenant_id", "connector_type"),
    )


class Writeback(TimestampMixin, Base):
    """A governed decision write-back job (INS-FR-061): a platform decision
    destined for a tenant's system of record via an `outgoing` connection.
    Delivery is proposal-mode (four-eyes) and idempotent per
    (tenant, connection, idempotency_key)."""

    __tablename__ = "writebacks"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    workspace_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    connection_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    decision_kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    decision_ref: Mapped[str] = mapped_column(sa.Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, default="pending_approval")
    approval_mode: Mapped[str] = mapped_column(sa.Text, nullable=False, default="four_eyes")
    requested_by: Mapped[str | None] = mapped_column(sa.Text)
    approved_by: Mapped[str | None] = mapped_column(sa.Text)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    target_ref: Mapped[str | None] = mapped_column(sa.Text)
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index(
            "uq_writebacks_tenant_conn_idem",
            "tenant_id",
            "connection_id",
            "idempotency_key",
            unique=True,
        ),
        sa.Index("ix_writebacks_tenant_status", "tenant_id", "status"),
        sa.Index("ix_writebacks_decision_ref", "tenant_id", "decision_ref"),
    )


class Ingestion(TimestampMixin, Base):
    __tablename__ = "ingestions"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    workspace_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    connection_id: Mapped[str | None] = mapped_column(UUIDType)
    dataset_urn: Mapped[str | None] = mapped_column(sa.Text)
    new_dataset: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    ingestion_mode: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_format: Mapped[str | None] = mapped_column(sa.Text)
    statement: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, default="created")
    trigger: Mapped[str] = mapped_column(sa.Text, nullable=False, default="manual")
    schedule_id: Mapped[str | None] = mapped_column(UUIDType)
    scheduled_for: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    skip_profiling: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    allow_empty: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    bytes_total: Mapped[int | None] = mapped_column(sa.BigInteger)
    bytes_received: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    rows_appended: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    iceberg_snapshot_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    error_log: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error_row_limit: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=100)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    retried_from_id: Mapped[str | None] = mapped_column(UUIDType)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(sa.Text)  # JWT sub; not necessarily a UUID

    __table_args__ = (
        sa.Index("ix_ingestions_tenant_status_created", "tenant_id", "status", "created_at"),
        sa.Index("ix_ingestions_tenant_dataset_created", "tenant_id", "dataset_urn", "created_at"),
        sa.Index(
            "ix_ingestions_connection_active",
            "connection_id",
            postgresql_where=sa.text("status NOT IN ('completed','failed','cancelled','expired')"),
            sqlite_where=sa.text("status NOT IN ('completed','failed','cancelled','expired')"),
        ),
    )


class Upload(TimestampMixin, Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    ingestion_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    part_size: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    storage_prefix: Mapped[str] = mapped_column(sa.Text, nullable=False)
    cloud_upload_id: Mapped[str | None] = mapped_column(sa.Text)
    sha256: Mapped[str | None] = mapped_column(sa.Text)
    bytes_total: Mapped[int | None] = mapped_column(sa.BigInteger)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, default="open")
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        sa.Index("ix_uploads_tenant_status_expires", "tenant_id", "status", "expires_at"),
    )


class UploadPart(Base):
    """Normalized part ledger (BRD models this as uploads.parts_confirmed jsonb;
    a table keeps duplicate/out-of-order part handling race-free — see README)."""

    __tablename__ = "upload_parts"

    upload_id: Mapped[str] = mapped_column(UUIDType, primary_key=True)
    n: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    etag: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )


class Schedule(TimestampMixin, Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    workspace_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    connection_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    ingestion_template: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    cron: Mapped[str | None] = mapped_column(sa.Text)
    interval_seconds: Mapped[int | None] = mapped_column(sa.Integer)
    timezone: Mapped[str] = mapped_column(sa.Text, nullable=False)
    watermark_column: Mapped[str | None] = mapped_column(sa.Text)
    watermark_operator: Mapped[str] = mapped_column(sa.Text, nullable=False, default=">")
    watermark_value_type: Mapped[str] = mapped_column(sa.Text, nullable=False, default="string")
    watermark_value: Mapped[str | None] = mapped_column(sa.Text)
    overlap_policy: Mapped[str] = mapped_column(sa.Text, nullable=False, default="skip")
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    temporal_schedule_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    last_fired_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(sa.Text)  # JWT sub; not necessarily a UUID
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (sa.Index("ix_schedules_tenant_enabled", "tenant_id", "enabled"),)


class IngestionTransition(Base):
    __tablename__ = "ingestion_transitions"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    ingestion_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    from_status: Mapped[str | None] = mapped_column(sa.Text)
    to_status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        sa.Index("ix_transitions_tenant_ingestion", "tenant_id", "ingestion_id", "created_at"),
    )


class WebhookEndpoint(TimestampMixin, Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    ingestion_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    path_token: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    hmac_vault_ref: Mapped[str] = mapped_column(sa.Text, nullable=False)
    flush_interval_s: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=60)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)


class WebhookEventDedup(Base):
    """BR-11: duplicate webhook deliveries deduped by client event_id (24h).
    Redis in prod (MASTER-FR-032); vendored as a table for wave-1."""

    __tablename__ = "webhook_event_dedup"

    ingestion_id: Mapped[str] = mapped_column(UUIDType, primary_key=True)
    event_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )


class OutboxEvent(Base):
    """Transactional outbox (MASTER-FR-034); envelope per MASTER-FR-031."""

    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    event_id: Mapped[str] = mapped_column(UUIDType, nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_urn: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    via_agent: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    occurred_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )
    trace_id: Mapped[str | None] = mapped_column(sa.Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index(
            "ix_outbox_unpublished",
            "occurred_at",
            postgresql_where=sa.text("published_at IS NULL"),
            sqlite_where=sa.text("published_at IS NULL"),
        ),
    )


class IdempotencyKey(Base):
    """MASTER-FR-025: POST idempotency (24h replay window)."""

    __tablename__ = "idempotency_keys"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=uuid7)
    tenant_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(sa.Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (sa.UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),)
