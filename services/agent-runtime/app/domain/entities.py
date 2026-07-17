"""Domain entities (BRD 14 §4). Plain dataclasses; the store maps them to rows."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

# ---- status enums (BRD 14 §4 state machines) --------------------------------
AGENT_STATUSES = ("draft", "published", "deprecated", "retired")
SESSION_STATUSES = ("active", "idle", "terminated", "expired")
RUN_STATUSES = (
    "queued", "running", "awaiting_input", "awaiting_approval",
    "completed", "failed", "cancelled", "killed", "expired",
)
RUN_TERMINAL = ("completed", "failed", "cancelled", "killed", "expired")
PROPOSAL_STATUSES = (
    "pending", "approved", "rejected", "edited_approved",
    "expired", "superseded", "cancelled",
)
PROPOSAL_TERMINAL = tuple(s for s in PROPOSAL_STATUSES if s != "pending")
# SLM distillation (milestone 3/4): a training job's lifecycle and a distilled
# adapter's promotion lifecycle (design doc §M3/M4).
TRAINING_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
TRAINING_TERMINAL = ("succeeded", "failed", "cancelled")
ADAPTER_PROMOTION_STATUSES = ("candidate", "gated", "promoted", "demoted")


def now() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    # uuid7 is time-ordered; uuid4 is a fine fallback for cursoring here.
    return str(uuid.uuid4())


@dataclass(slots=True)
class AgentDefinition:
    agent_key: str
    display_name: str
    description: str
    owner_team: str
    default_write_mode: str  # read_only | proposal
    status: str = "draft"
    # BRD 53: NULL = platform agent (global); set = a tenant CUSTOM agent,
    # visible + runnable only within its authoring tenant.
    owner_tenant: str | None = None


@dataclass(slots=True)
class AgentVersion:
    agent_key: str
    version: int
    graph_ref: str
    graph_digest: str
    prompt_refs: list[dict] = field(default_factory=list)
    toolset: list[dict] = field(default_factory=list)
    model_config: dict = field(default_factory=dict)
    guardrail_profile: str = "standard"
    memory_policy: dict = field(default_factory=dict)
    eval_gate: dict = field(default_factory=dict)
    eval_gate_result_id: str | None = None
    a2a_card: dict = field(default_factory=dict)
    card_signature: str | None = None
    principal_ref: str | None = None
    status: str = "draft"


@dataclass(slots=True)
class TenantAgentConfig:
    tenant_id: str
    agent_key: str
    enabled: bool = True
    pinned_version: int | None = None
    prompt_params: dict = field(default_factory=dict)
    auto_execute_policy: dict = field(default_factory=dict)
    self_approval: bool = False


@dataclass(slots=True)
class Rollout:
    rollout_id: str
    agent_key: str
    cell: str
    mode: str  # direct | canary | shadow
    candidate_version: int
    baseline_version: int
    pct: int = 0
    tenant_filter: dict = field(default_factory=dict)
    status: str = "active"  # active | promoted | rolled_back


@dataclass(slots=True)
class Session:
    session_id: str
    tenant_id: str
    user_id: str | None
    agent_key: str
    agent_version: int
    context_urn: str | None
    status: str
    created_at: datetime
    last_activity_at: datetime
    expires_hard_at: datetime


@dataclass(slots=True)
class Run:
    run_id: str
    tenant_id: str
    session_id: str
    agent_key: str
    agent_version: int
    temporal_workflow_id: str | None
    status: str
    principal_type: str  # user_obo | agent_autonomous
    obo_sub: str | None = None
    parent_run_id: str | None = None
    usage: dict = field(default_factory=dict)
    error: dict | None = None
    # Final assistant answer, persisted on completion so non-streaming clients
    # can read it back from GET /api/v1/runs/{id}.
    final_text: str | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass(slots=True)
class Proposal:
    proposal_id: str
    tenant_id: str
    session_id: str | None
    run_id: str
    agent_key: str
    agent_version: int
    obo_user: str | None
    tool_id: str
    tool_version: str
    tier: str
    side_effects: str  # none | reversible | destructive
    args: dict
    rationale: str
    affected_urns: list[str]
    predicted_effect: dict
    expires_at: datetime
    status: str = "pending"
    decision: dict | None = None
    # Workspace context for workspace-scoped authz checks (case.case.update,
    # ai.proposal.approve), kept OUT of args: args is sent verbatim to
    # tool-plane, and a strict-schema tool (additionalProperties:false) would
    # reject a workspace_id field it never declared. service.py falls back to
    # args.get("workspace_id") when this is None, for graphs whose tools do
    # accept workspace_id as a real arg.
    workspace_id: str | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass(slots=True)
class Transcript:
    """A governed record of one completed agent run for SLM distillation
    (docs/design/slm-distillation.md). Captured PII-redacted + consent-gated at
    run completion; the human ``decision``/``corrected_output`` are joined in
    when the run's proposal is decided — an approved/edited proposal is a gold
    (input -> corrected-output) training pair."""

    transcript_id: str
    tenant_id: str
    run_id: str
    session_id: str | None
    agent_key: str
    agent_version: int
    principal_type: str
    obo_sub: str | None
    inputs: dict
    grounding: dict
    final_text: str | None
    proposed_action: dict | None
    proposal_id: str | None
    model: str | None
    usage: dict
    consent: bool
    decision: str | None = None            # approve | edit | reject | cancel
    corrected_output: dict | None = None   # the human-corrected args (edit)
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass(slots=True)
class SftExample:
    """One frozen chat-format training row in a built SFT dataset (milestone 2).
    ``messages`` is the OpenAI-style chat array (system/user/assistant); the
    assistant turn is the gold target (the human-corrected action for an edited
    proposal, or the approved action)."""

    dataset_id: str
    tenant_id: str
    ord: int
    messages: list[dict]
    target_kind: str  # approve | edit
    source_transcript_id: str | None
    example_hash: str
    created_at: datetime = field(default_factory=now)


@dataclass(slots=True)
class SftDataset:
    """A governed, versioned SFT dataset built by curating the transcript corpus
    (milestone 2). Immutable once built; re-curation mints a new version."""

    dataset_id: str
    tenant_id: str
    agent_key: str
    version: int
    status: str  # built
    row_count: int
    source_count: int
    curation_params: dict
    checksum: str
    consent_verified: bool
    created_by: str | None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass(slots=True)
class TrainingJob:
    """A submitted SLM distillation run against a versioned SFT dataset
    (milestone 3). The control-plane row; the GPU LoRA compute runs behind the
    ``GpuTrainer`` port. Immutable identity; status advances through
    ``TRAINING_STATUSES``."""

    job_id: str
    tenant_id: str
    archetype: str
    sft_dataset_id: str
    base_model: str
    status: str  # queued | running | succeeded | failed | cancelled
    params: dict
    mlflow_run_ref: str | None = None
    adapter_id: str | None = None
    error: dict | None = None
    created_by: str | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class SlmAdapter:
    """A distilled adapter produced by a succeeded training job (milestone 3),
    with its promotion lifecycle (milestone 4). Once promoted it becomes the
    tenant's cheapest ai-gateway ladder rung (``model_alias``)."""

    adapter_id: str
    tenant_id: str
    training_job_id: str
    archetype: str
    base_model: str
    adapter_uri: str
    model_alias: str
    checksum: str = ""
    promotion_status: str = "candidate"  # candidate | gated | promoted | demoted
    eval_result_ref: str | None = None
    target_rung_alias: str | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass(slots=True)
class KillSwitch:
    kill_id: str
    scope: str  # agent | agent_version | agent_version_tenant
    agent_key: str
    version: int | None
    tenant_id: str | None
    active: bool
    reason: str
    set_by: str
    created_at: datetime | None = None
