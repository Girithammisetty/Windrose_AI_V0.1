"""Request models + response envelope helpers (MASTER-FR-022)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


def data(payload: Any, **extra) -> dict:
    out = {"data": payload}
    out.update(extra)
    return out


class DatasetCreate(BaseModel):
    dataset_key: str
    agent_key: str
    description: str | None = None
    provenance_summary: dict = Field(default_factory=dict)


class CaseCreate(BaseModel):
    dataset_key: str
    agent_key: str = "unknown"
    input: dict
    expected: dict
    source: str = "manual"
    source_ref: str | None = None
    tags: list[str] = Field(default_factory=list)
    weight: float = 1.0
    status: str = "candidate"
    anonymization_attested_by: str | None = None


class CasePatch(BaseModel):
    input: dict | None = None
    expected: dict | None = None
    tags: list[str] | None = None
    weight: float | None = None
    anonymization_attested_by: str | None = None


class AttestBody(BaseModel):
    attested_by: str


class ScorerCreate(BaseModel):
    scorer_key: str
    version: int
    kind: str
    gate_eligible: bool = True
    config_schema: dict = Field(default_factory=dict)
    applicable_expected_kinds: list[str] = Field(default_factory=list)
    image_ref: str | None = None
    judge_prompt_ref: str | None = None
    judge_prompt_ver: str | None = None
    judge_agreement: float | None = None
    status: str = "draft"


class ScorerPatch(BaseModel):
    gate_eligible: bool | None = None
    config_schema: dict | None = None
    applicable_expected_kinds: list[str] | None = None
    image_ref: str | None = None
    judge_prompt_ref: str | None = None
    judge_prompt_ver: str | None = None
    judge_agreement: float | None = None
    status: str | None = None


class SuiteCreate(BaseModel):
    suite_id: str
    agent_key: str
    datasets: list[dict]
    scorers: list[dict]
    gate_rule: str
    baseline_version: str | None = None
    judge_ladder_pin: dict = Field(default_factory=dict)
    min_cases: int = 0


class SuitePatch(BaseModel):
    datasets: list[dict] | None = None
    scorers: list[dict] | None = None
    gate_rule: str | None = None
    baseline_version: str | None = None
    judge_ladder_pin: dict | None = None
    min_cases: int | None = None


class RunCreate(BaseModel):
    trigger: str = "manual"
    agent_key: str
    candidate: dict
    suite_id: str
    suite_version: int | None = None
    candidate_outputs: dict[str, dict] = Field(default_factory=dict)
    baseline: dict | None = None
    memory_snapshot_ver: str | None = None
    cost_cap_usd: float | None = None


class CiEvaluate(BaseModel):
    repo: str | None = None
    commit: str | None = None
    agent_key: str
    build_digest: str
    suite_id: str
    suite_version: int | None = None
    candidate_outputs: dict[str, dict] = Field(default_factory=dict)
    baseline: dict | None = None
    tenant_id: str | None = None  # SPIFFE (mTLS) CI callers only


class CanaryCreate(BaseModel):
    agent_key: str
    candidate_version: str
    baseline_version: str
    mode: str = "paired_shadow"
    sample_spec: dict = Field(default_factory=lambda: {"min_samples": 200})
    thresholds: dict = Field(default_factory=dict)
    must_scorers: list[str] = Field(default_factory=list)


class CanarySamples(BaseModel):
    paired_scores: dict[str, list[list[float]]]


class SloTargets(BaseModel):
    agent_key: str
    agent_version: str | None = None
    targets: dict
