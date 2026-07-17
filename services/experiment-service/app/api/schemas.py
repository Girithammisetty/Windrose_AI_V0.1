"""Pydantic request models + response envelope helpers (BRD §5)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExperimentCreate(BaseModel):
    workspace_id: str
    name: str
    model_type: str
    model_pipeline_urn: str
    feature_engineering_pipeline_urn: str
    training_pipeline_urn: str
    description: str | None = None
    note: str | None = None
    tags: dict = Field(default_factory=dict)


class ExperimentPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    note: str | None = None
    tags: dict | None = None


class RunPatch(BaseModel):
    name: str | None = None
    note: str | None = None
    tags: dict | None = None


class NoteBody(BaseModel):
    description: str


class CompareRequest(BaseModel):
    run_ids: list[str]
    metrics: list[str] | None = None
    params: list[str] | None = None
    include_all: bool = False


class RegisterRequest(BaseModel):
    model_name: str
    owner_id: str | None = None
    description: str | None = None
    flavor: str | None = None
    mlflow_model_ref: str | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None


class PromoteRequest(BaseModel):
    target_stage: str
    rationale: str | None = None


class DecisionRequest(BaseModel):
    decision: str
    message: str | None = None
    target_stage: str | None = None


class CardPatch(BaseModel):
    intended_use: str | None = None
    limitations: str | None = None
    evaluation_summary: str | None = None
    ethical_considerations: str | None = None


def page_envelope(items: list, next_cursor: str | None, has_more: bool) -> dict:
    return {"data": items, "page": {"next_cursor": next_cursor, "has_more": has_more}}
