"""Domain entities (BRD §4). Plain dataclasses; the store maps them to rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# --------------------------------------------------------------------- enums


class DatasetStatus(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"
    ARCHIVED = "archived"


class CaseStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    RETIRED = "retired"


class CaseSource(StrEnum):
    VERIFIED_QUERY = "verified_query"
    PRODUCTION_TRACE = "production_trace"
    HITL_REJECTION = "hitl_rejection"
    APPROVAL_EDIT_DIFF = "approval_edit_diff"
    MANUAL = "manual"


class ExpectedKind(StrEnum):
    SQL_RESULT = "sql_result"
    TOOL_SEQUENCE = "tool_sequence"
    PROPOSAL = "proposal"
    STRUCTURED = "structured"
    RUBRIC = "rubric"


class ScorerKind(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM_JUDGE = "llm_judge"


class RunTrigger(StrEnum):
    CI = "ci"
    PUBLISH_GATE = "publish_gate"
    SCHEDULED_ONLINE = "scheduled_online"
    CANARY = "canary"
    MANUAL = "manual"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SCORING = "scoring"
    COMPLETED = "completed"
    FAILED = "failed"


class CanaryStatus(StrEnum):
    COLLECTING = "collecting"
    READY = "ready"
    FAILED_EARLY = "failed_early"
    EXPIRED = "expired"


# --------------------------------------------------------------------- entities


@dataclass
class Dataset:
    id: str
    tenant_id: str
    dataset_key: str  # e.g. analytics/nl2sql
    agent_key: str
    version: int
    status: str
    description: str | None
    case_count: int
    provenance_summary: dict
    frozen_by: str | None
    frozen_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass
class EvalCase:
    id: str
    tenant_id: str
    dataset_key: str
    dataset_version: int
    input: dict
    expected: dict  # {kind, value}
    source: str
    source_ref: str | None
    source_tenant_id: str | None
    tags: list[str]
    weight: float
    status: str
    anonymization_attested_by: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class Scorer:
    id: str
    tenant_id: str
    scorer_key: str
    version: int
    kind: str
    gate_eligible: bool
    config_schema: dict
    applicable_expected_kinds: list[str]
    image_ref: str | None
    judge_prompt_ref: str | None
    judge_prompt_ver: str | None
    judge_agreement: float | None
    status: str  # draft | active | retired
    created_at: datetime


@dataclass
class Suite:
    id: str
    tenant_id: str
    suite_id: str
    agent_key: str
    version: int
    datasets: list[dict]  # [{dataset_key, version}]
    scorers: list[dict]  # [{scorer, version, weight, regression_threshold, config}]
    gate_rule: str
    baseline_version: str | None
    judge_ladder_pin: dict
    min_cases: int
    created_at: datetime


@dataclass
class EvalRun:
    id: str
    tenant_id: str
    trigger: str
    agent_key: str
    candidate: dict  # {agent_version?, content_digest}
    baseline: dict | None
    suite_pins: dict  # {suite_id, suite_version, datasets, scorers, judge_ladder}
    memory_snapshot_ver: str | None
    status: str
    totals: dict
    cost_usd: float
    cost_cap_usd: float
    temporal_workflow_id: str | None
    started_by: str
    created_at: datetime
    updated_at: datetime


@dataclass
class CaseResult:
    id: str
    tenant_id: str
    run_id: str
    case_id: str
    scorer_key: str
    scorer_version: int
    score: float
    passed: bool
    details: dict
    trace_ref: str | None
    latency_ms: int | None
    cost_usd: float
    weight: float
    created_at: datetime


@dataclass
class GateResult:
    id: str
    tenant_id: str
    gate_run_id: str
    run_id: str
    agent_key: str
    content_digest: str
    suite_id: str
    suite_version: int
    dataset_version: int
    gate_passed: bool
    verdicts: list[dict]
    failed_cases_sample: list[dict]
    report_url: str | None
    created_at: datetime


@dataclass
class CanaryComparison:
    id: str
    tenant_id: str
    comparison_id: str
    agent_key: str
    candidate_version: str
    baseline_version: str
    sample_spec: dict
    mode: str
    status: str
    report: dict
    samples: int
    created_at: datetime
    updated_at: datetime


@dataclass
class SloRollup:
    id: str
    tenant_id: str | None
    agent_key: str
    agent_version: str | None
    window: str
    window_start: datetime
    counters: dict
    targets: dict
    sample_n: int
    created_at: datetime
    updated_at: datetime


@dataclass
class Page:
    items: list
    next_cursor: str | None
    has_more: bool = False


@dataclass
class CallCtx:
    tenant_id: str
    actor: dict
    via_agent: dict | None = None
    trace_id: str | None = None
    scopes: list[str] = field(default_factory=list)
