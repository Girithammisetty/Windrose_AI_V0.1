"""SQLAlchemy 2 ORM models (BRD §4). Every tenant table carries ``tenant_id``
and is protected by Postgres RLS (MASTER-FR-001); platform-scoped rows use the
platform tenant id but still live under RLS."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DatasetRow(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String, nullable=False)
    agent_key: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    case_count: Mapped[int] = mapped_column(Integer, default=0)
    provenance_summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    frozen_by: Mapped[str | None] = mapped_column(String)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvalCaseRow(Base):
    __tablename__ = "eval_cases"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String, nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expected: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String)
    source_tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    tags: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String, nullable=False)
    anonymization_attested_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScorerRow(Base):
    __tablename__ = "scorers"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    scorer_key: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    gate_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    config_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    applicable_expected_kinds: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    image_ref: Mapped[str | None] = mapped_column(String)
    judge_prompt_ref: Mapped[str | None] = mapped_column(String)
    judge_prompt_ver: Mapped[str | None] = mapped_column(String)
    judge_agreement: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SuiteRow(Base):
    __tablename__ = "suites"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    suite_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_key: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    datasets: Mapped[list] = mapped_column(JSONB, default=list)
    scorers: Mapped[list] = mapped_column(JSONB, default=list)
    gate_rule: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_version: Mapped[str | None] = mapped_column(String)
    judge_ladder_pin: Mapped[dict] = mapped_column(JSONB, default=dict)
    min_cases: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvalRunRow(Base):
    __tablename__ = "eval_runs"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    agent_key: Mapped[str] = mapped_column(String, nullable=False)
    candidate: Mapped[dict] = mapped_column(JSONB, default=dict)
    baseline: Mapped[dict | None] = mapped_column(JSONB)
    suite_pins: Mapped[dict] = mapped_column(JSONB, default=dict)
    memory_snapshot_ver: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    totals: Mapped[dict] = mapped_column(JSONB, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_cap_usd: Mapped[float] = mapped_column(Float, default=0.0)
    temporal_workflow_id: Mapped[str | None] = mapped_column(String)
    started_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseResultRow(Base):
    __tablename__ = "case_results"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    case_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    scorer_key: Mapped[str] = mapped_column(String, nullable=False)
    scorer_version: Mapped[int] = mapped_column(Integer, default=1)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    trace_ref: Mapped[str | None] = mapped_column(String)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GateResultRow(Base):
    __tablename__ = "gate_results"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    gate_run_id: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    agent_key: Mapped[str] = mapped_column(String, nullable=False)
    content_digest: Mapped[str] = mapped_column(String, nullable=False)
    suite_id: Mapped[str] = mapped_column(String, nullable=False)
    suite_version: Mapped[int] = mapped_column(Integer, default=1)
    dataset_version: Mapped[int] = mapped_column(Integer, default=1)
    gate_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    verdicts: Mapped[list] = mapped_column(JSONB, default=list)
    failed_cases_sample: Mapped[list] = mapped_column(JSONB, default=list)
    report_url: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CanaryRow(Base):
    __tablename__ = "canary_comparisons"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    comparison_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_key: Mapped[str] = mapped_column(String, nullable=False)
    candidate_version: Mapped[str] = mapped_column(String, nullable=False)
    baseline_version: Mapped[str] = mapped_column(String, nullable=False)
    sample_spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SloRollupRow(Base):
    __tablename__ = "slo_rollups"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    agent_key: Mapped[str] = mapped_column(String, nullable=False)
    agent_version: Mapped[str | None] = mapped_column(String)
    window: Mapped[str] = mapped_column("window_name", String, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    counters: Mapped[dict] = mapped_column(JSONB, default=dict)
    targets: Mapped[dict] = mapped_column(JSONB, default=dict)
    sample_n: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxRow(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    topic: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessedEventRow(Base):
    __tablename__ = "processed_events"
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
