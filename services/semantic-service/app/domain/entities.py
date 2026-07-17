"""Domain entities (BRD 06 §4.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class SemanticModel:
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    description: str | None
    published_version_id: str | None
    health: dict | None  # {"status": "ok"|"broken", "broken_refs": [...]}
    created_by: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


VERSION_STATUSES = ("draft", "in_review", "published", "rejected", "superseded")


@dataclass(slots=True)
class ModelVersion:
    id: str
    tenant_id: str
    model_id: str
    version_no: int
    status: str
    definition: dict
    diff: dict | None
    submitted_by: str | None
    approved_by: str | None
    decision_note: str | None
    published_at: datetime | None
    created_at: datetime


VQ_STATUSES = ("draft", "pending_review", "approved", "rejected", "archived")


@dataclass(slots=True)
class VerifiedQuery:
    id: str
    tenant_id: str
    workspace_id: str
    model_id: str | None
    nl_text: str
    sql_text: str
    variables: list[dict]
    status: str
    tags: list[str]
    provenance: dict | None
    health_note: str | None
    embedding: list[float] | None
    submitted_by: str
    approved_by: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(slots=True)
class Operation:
    id: str
    tenant_id: str
    kind: str
    status: str  # running | completed | failed
    resource_urn: str
    report: dict | None
    created_at: datetime
    finished_at: datetime | None = None


@dataclass(slots=True)
class CompileLogEntry:
    id: str
    tenant_id: str
    model_version_id: str
    request_hash: str
    request: dict
    caller_class: str  # api | chart | agent_tool
    dialect: str
    warnings: list = field(default_factory=list)
    duration_ms: int = 0
    created_at: datetime | None = None


@dataclass(slots=True)
class ChartRef:
    """Reverse index chart -> measures (deprecation impact, BRD §6 consumed)."""
    tenant_id: str
    chart_urn: str
    model: str | None
    measures: list[str]
