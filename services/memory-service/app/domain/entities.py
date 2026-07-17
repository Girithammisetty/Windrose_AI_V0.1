"""Core domain entities (MEM-FR-001, §4 data model)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Scope constants
SCOPE_SESSION = "session"
SCOPE_USER = "user"
SCOPE_WORKSPACE = "workspace"
SCOPE_TENANT = "tenant"
DURABLE_SCOPES = (SCOPE_USER, SCOPE_WORKSPACE, SCOPE_TENANT)
ALL_SCOPES = (SCOPE_SESSION, SCOPE_USER, SCOPE_WORKSPACE, SCOPE_TENANT)

# Status constants
STATUS_ACTIVE = "active"
STATUS_QUARANTINED = "quarantined"
STATUS_EXPIRED = "expired"
STATUS_DELETED = "deleted"

# Provenance source types
SRC_AGENT_RUN = "agent_run"
SRC_USER_EXPLICIT = "user_explicit"
SRC_TOOL_OUTPUT = "tool_output"
SRC_ADMIN = "admin"
SOURCE_TYPES = (SRC_AGENT_RUN, SRC_USER_EXPLICIT, SRC_TOOL_OUTPUT, SRC_ADMIN)


@dataclass
class Provenance:
    source_type: str
    run_id: str | None = None
    agent_key: str | None = None
    agent_version: str | None = None
    user_id: str | None = None
    tool_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "run_id": self.run_id,
            "agent_key": self.agent_key,
            "agent_version": self.agent_version,
            "user_id": self.user_id,
            "tool_id": self.tool_id,
        }

    @staticmethod
    def from_dict(d: dict) -> Provenance:
        return Provenance(
            source_type=d.get("source_type", SRC_AGENT_RUN),
            run_id=d.get("run_id"),
            agent_key=d.get("agent_key"),
            agent_version=d.get("agent_version"),
            user_id=d.get("user_id"),
            tool_id=d.get("tool_id"),
        )


@dataclass
class MemoryRecord:
    memory_id: str
    tenant_id: str
    scope: str
    scope_ref: str
    content: str
    embedding: list[float] | None
    provenance: list[dict]  # append-only provenance log (§4 merge appends entries)
    confidence: float
    ttl_expires_at: datetime
    revalidate_at: datetime
    status: str = STATUS_ACTIVE
    tags: list[str] = field(default_factory=list)
    retrieval_count: int = 0
    last_retrieved_at: datetime | None = None
    classifier_score: float | None = None
    merged_from: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def primary_provenance(self) -> dict:
        return self.provenance[0] if self.provenance else {}


@dataclass
class RagChunk:
    chunk_id: str
    tenant_id: str
    corpus_key: str
    source_urn: str
    chunk_seq: int
    content: str
    embedding: list[float] | None
    embedding_model_ver: str
    snapshot_ver: str | None
    source_updated_at: datetime | None
    user_linkage: str | None = None  # user_id whose content this chunk derives from
    created_at: datetime | None = None


@dataclass
class Corpus:
    corpus_key: str
    tenant_id: str
    source: dict  # {kind, topics|none}
    chunking: dict  # {strategy, max_tokens, overlap}
    active_embedding_ver: str
    refresh: dict  # {mode}
    anonymization_profile: dict | None
    status: str = "active"  # active|paused|rebuilding
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class TenantPolicy:
    tenant_id: str
    ttl_overrides: dict = field(default_factory=dict)  # {"user":"P90D",...}
    pii_classes: list[str] = field(default_factory=list)
    injection_profile: str = "standard"
    corpus_flags: dict = field(default_factory=dict)
    updated_at: datetime | None = None


@dataclass
class ErasureRequest:
    request_id: str
    tenant_id: str
    subject_type: str
    subject_id: str
    status: str  # received|running|verifying|completed|failed
    workflow_id: str | None = None
    report: dict | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class ScoredResult:
    kind: str  # "memory" | "chunk"
    content: str
    score: float
    scope: str | None = None
    memory_id: str | None = None
    chunk_id: str | None = None
    corpus: str | None = None
    provenance: dict | None = None
    source_urn: str | None = None
    snapshot_ver: str | None = None
    debug: dict | None = None
