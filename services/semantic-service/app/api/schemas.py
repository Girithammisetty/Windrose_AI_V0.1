"""Request models + response envelope helpers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelCreate(BaseModel):
    workspace_id: str
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    definition: dict | None = None


class ModelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class DefinitionPatch(BaseModel):
    definition: dict


class DecisionBody(BaseModel):
    note: str | None = None


class BootstrapBody(BaseModel):
    sources: dict = Field(default_factory=dict)
    workspace: str | None = None


class VerifiedQueryCreate(BaseModel):
    workspace_id: str
    nl_text: str = Field(min_length=1)
    sql_text: str = Field(min_length=1)
    variables: list[dict] = Field(default_factory=list)
    model: str | None = None
    tags: list[str] = Field(default_factory=list)


class VerifiedQueryPatch(BaseModel):
    nl_text: str | None = None
    sql_text: str | None = None
    variables: list[dict] | None = None
    tags: list[str] | None = None


class CandidateCreate(BaseModel):
    """SEM-FR-042: harvested candidates from eval-service/agent-runtime."""
    workspace_id: str
    nl_text: str = Field(min_length=1)
    sql_text: str = Field(min_length=1)
    variables: list[dict] = Field(default_factory=list)
    model: str | None = None
    tags: list[str] = Field(default_factory=list)
    agent_run_urn: str


def page_envelope(items: list, next_cursor: str | None, has_more: bool) -> dict:
    return {"data": items, "page": {"next_cursor": next_cursor, "has_more": has_more}}
