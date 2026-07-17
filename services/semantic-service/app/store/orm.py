"""SQLAlchemy ORM models (BRD 06 §4.1).

Deviation notes (README): `compile_log` is specced monthly-partitioned with
6-month retention; native partitioning is deferred (TODO) — retention job
enforces the window. `verified_queries.embedding` is a pgvector `vector(768)`
column in the migration (768 = nomic-embed-text); the ORM maps it via a pgvector
column type (asyncpg round-trips pgvector
values as strings; ANN search goes through raw SQL with a hard tenant filter).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    """Minimal pgvector column type: values travel as '[x,y,...]' literals.
    ANN search goes through raw SQL with explicit CAST(:emb AS vector)."""

    cache_ok = True

    def __init__(self, dim: int):
        self.dim = dim

    def get_col_spec(self, **kw) -> str:
        return f"vector({self.dim})"

    def bind_processor(self, dialect):
        return lambda value: value

    def result_processor(self, dialect, coltype):
        return lambda value: value


class Base(DeclarativeBase):
    pass


class SemanticModelRow(Base):
    __tablename__ = "semantic_models"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    published_version_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    health: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelVersionRow(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version_no"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    diff: Mapped[dict | None] = mapped_column(JSONB)
    submitted_by: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Normalized projections rebuilt from the published definition (BRD §4.1)


class EntityRow(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("model_version_id", "name"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_urn: Mapped[str] = mapped_column(Text, nullable=False)
    physical_table: Mapped[str] = mapped_column(Text, nullable=False)
    version_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    primary_key: Mapped[list] = mapped_column(JSONB, nullable=False)


class DimensionRow(Base):
    __tablename__ = "dimensions"
    __table_args__ = (UniqueConstraint("model_version_id", "name"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    entity_name: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    column: Mapped[str | None] = mapped_column("column", Text)
    expr_ast: Mapped[dict | None] = mapped_column(JSONB)
    dim_type: Mapped[str] = mapped_column(Text, nullable=False)
    time_grains: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    synonyms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    deprecated: Mapped[bool] = mapped_column(nullable=False, default=False)
    successor: Mapped[str | None] = mapped_column(Text)


class MeasureRow(Base):
    __tablename__ = "measures"
    __table_args__ = (UniqueConstraint("model_version_id", "name"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    entity_name: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    agg: Mapped[str | None] = mapped_column(Text)
    expr_ast: Mapped[dict | None] = mapped_column(JSONB)
    filters_ast: Mapped[dict | None] = mapped_column(JSONB)
    synonyms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    deprecated: Mapped[bool] = mapped_column(nullable=False, default=False)
    successor: Mapped[str | None] = mapped_column(Text)


class JoinPathRow(Base):
    __tablename__ = "join_paths"
    __table_args__ = (UniqueConstraint("model_version_id", "name"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    from_entity: Mapped[str] = mapped_column(Text, nullable=False)
    to_entity: Mapped[str] = mapped_column(Text, nullable=False)
    join_type: Mapped[str] = mapped_column(Text, nullable=False)
    on_pairs: Mapped[list] = mapped_column(JSONB, nullable=False)
    cardinality: Mapped[str] = mapped_column(Text, nullable=False)


class VerifiedQueryRow(Base):
    __tablename__ = "verified_queries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    model_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    nl_text: Mapped[str] = mapped_column(Text, nullable=False)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    provenance: Mapped[dict | None] = mapped_column(JSONB)
    health_note: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[str | None] = mapped_column(Vector(768))
    submitted_by: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompileLogRow(Base):
    __tablename__ = "compile_log"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    model_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)
    caller_class: Mapped[str] = mapped_column(Text, nullable=False)
    dialect: Mapped[str] = mapped_column(Text, nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperationRow(Base):
    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    resource_urn: Mapped[str] = mapped_column(Text, nullable=False)
    report: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChartRefRow(Base):
    __tablename__ = "chart_refs"

    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    chart_urn: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str | None] = mapped_column(Text)
    measures: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)


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
