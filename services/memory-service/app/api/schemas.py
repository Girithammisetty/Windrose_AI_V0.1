"""Pydantic request/response models (MASTER-FR-020..024)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


def data_envelope(data: Any, page: dict | None = None, **extra) -> dict:
    body = {"data": data, **extra}
    if page is not None:
        body["page"] = page
    return body


class ProvenanceIn(BaseModel):
    source_type: str
    run_id: str | None = None
    agent_key: str | None = None
    agent_version: str | None = None
    user_id: str | None = None
    tool_id: str | None = None


class WriteMemoryIn(BaseModel):
    scope: str
    scope_ref: str
    content: str
    provenance: ProvenanceIn
    confidence: float | None = None
    tags: list[str] = Field(default_factory=list)


class BatchWriteIn(BaseModel):
    items: list[WriteMemoryIn]


class RetrieveIn(BaseModel):
    query_text: str | None = None
    query_embedding: list[float] | None = None
    scopes: list[str] = Field(default_factory=list)
    scope_refs: dict[str, str] = Field(default_factory=dict)
    corpora: list[str] = Field(default_factory=list)
    top_k: int = 8
    min_confidence: float | None = None
    tags: list[str] | None = None
    snapshot_ver: str | None = None
    include_debug: bool = False


class EditMemoryIn(BaseModel):
    content: str


class UnquarantineIn(BaseModel):
    reason: str


class ErasureIn(BaseModel):
    subject_type: str = "user"
    subject_id: str


class PolicyIn(BaseModel):
    ttl_overrides: dict[str, str] = Field(default_factory=dict)
    pii_classes: list[str] = Field(default_factory=list)
    injection_profile: str = "standard"
    corpus_flags: dict[str, bool] = Field(default_factory=dict)


class CorpusIn(BaseModel):
    corpus_key: str
    source: dict | None = None
    chunking: dict | None = None
    embedding_model_ver: str | None = None
    refresh: dict | None = None
    anonymization_profile: dict | None = None


class CorpusPatchIn(BaseModel):
    source: dict | None = None
    chunking: dict | None = None
    refresh: dict | None = None
    anonymization_profile: dict | None = None
    status: str | None = None


class RebuildIn(BaseModel):
    embedding_model_ver: str


class DocumentIn(BaseModel):
    source_urn: str
    content: str
