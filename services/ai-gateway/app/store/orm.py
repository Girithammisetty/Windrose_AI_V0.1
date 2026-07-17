"""SQLAlchemy ORM models (BRD 12 §4).

Deviation note (documented in README): budget_spend / request_log are specced
as monthly partitioned; native partitioning is deferred (TODO) because the
partition key would join every unique constraint. Retention jobs enforce the
windows instead (24 months / 90 days)."""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1536


class Base(DeclarativeBase):
    pass


class ProviderDeploymentRow(Base):
    __tablename__ = "provider_deployments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_family: Mapped[str] = mapped_column(Text, nullable=False)
    deployment_name: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    cloud: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_vault_ref: Mapped[str] = mapped_column(Text, nullable=False)
    tpm_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rpm_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelLadderRow(Base):
    __tablename__ = "model_ladders"
    __table_args__ = (UniqueConstraint("tenant_id", "request_class", "scope"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    request_class: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    rungs: Mapped[list] = mapped_column(JSONB, nullable=False)  # documented ≤8KB
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_rung: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BudgetRow(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("tenant_id", "scope_type", "scope_ref", "window"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_ref: Mapped[str] = mapped_column(Text, nullable=False)
    window: Mapped[str] = mapped_column("window", Text, nullable=False)
    limit_usd: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    degrade_pct: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=95)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BudgetSpendRow(Base):
    """Ledger source of truth; keyed by budget_ref (uuid or synthesized
    default-budget id) + window_start ISO date."""

    __tablename__ = "budget_spend"
    __table_args__ = (UniqueConstraint("budget_ref", "window_start"),)

    budget_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[str] = mapped_column(Text, primary_key=True)
    spend_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BudgetReservationRow(Base):
    __tablename__ = "budget_reservations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    budget_ref: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    window_start: Mapped[str] = mapped_column(Text, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BudgetThresholdFlagRow(Base):
    __tablename__ = "budget_threshold_flags"

    flag_key: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VirtualKeyRow(Base):
    __tablename__ = "virtual_keys"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    principal_type: Mapped[str] = mapped_column(Text, nullable=False)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_request_classes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    max_rung: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GuardrailPolicyRow(Base):
    __tablename__ = "guardrail_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "version"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    policy: Mapped[dict] = mapped_column(JSONB, nullable=False)  # documented ≤8KB
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SemanticCacheEntryRow(Base):
    __tablename__ = "semantic_cache_entries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    prompt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIM))
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RequestLogRow(Base):
    __tablename__ = "request_log"

    request_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    principal: Mapped[str] = mapped_column(Text, nullable=False)
    request_class: Mapped[str] = mapped_column(Text, nullable=False)
    model_alias: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rung: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    guardrail_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False,
                                                       default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_id: Mapped[str | None] = mapped_column(Text)
    deployment_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenantConfigRow(Base):
    __tablename__ = "tenant_configs"

    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    cell_cloud: Mapped[str | None] = mapped_column(Text)
    cache_ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxRow(Base):
    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessedEventRow(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
