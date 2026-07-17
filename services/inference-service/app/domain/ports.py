"""Port protocols the domain services depend on (hexagonal boundaries).

Real adapters (MLflow registry, object-store dataset gateway, local scoring
executor, Kafka bus, Redis dedup, SQL UoW) implement these; unit tests supply
in-memory doubles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.domain.schema_compat import ModelInputColumn


@dataclass
class CallCtx:
    tenant_id: str
    actor: dict
    via_agent: dict | None = None
    trace_id: str | None = None
    workspace_id: str | None = None
    submitted_by: str = ""


@dataclass
class ResolvedModel:
    name: str
    version: int
    stage: str  # production|staging|archived|none
    model_uri: str  # models:/<name>/<version>
    inputs: list[ModelInputColumn]
    model_id: str  # registered model identifier (for URN)
    run_id: str | None = None


@dataclass
class ResolvedDataset:
    urn: str
    dataset_id: str
    version: int
    schema: dict[str, dict]  # col -> {type, nullable}
    row_count: int
    storage_uri: str  # s3://bucket/key.parquet (input parquet location)


@dataclass
class ScoringResult:
    output_storage_uri: str
    snapshot_id: str
    row_count: int
    prediction_columns: list[str]


@dataclass
class Filters:
    status: int | None = None
    model_version_urn: str | None = None
    schedule_id: str | None = None


@dataclass
class Page:
    items: list = field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


@runtime_checkable
class ModelRegistry(Protocol):
    """Resolves registered model versions from the real MLflow registry."""

    async def resolve_version(self, name: str, version: int) -> ResolvedModel: ...

    async def resolve_by_stage(self, name: str, stage: str) -> ResolvedModel | None: ...


@runtime_checkable
class ScoringExecutor(Protocol):
    """Runs the real scoring: loads the model from MLflow, predicts on the real
    input data, writes a single-snapshot output parquet, returns its pointer.
    This is the local real substitute for the pipeline-orchestrator/Argo run."""

    async def run(
        self, *, model: ResolvedModel, dataset: ResolvedDataset, job, parameters: dict
    ) -> ScoringResult: ...


@runtime_checkable
class Dedup(Protocol):
    async def already_processed(self, tenant_id: str, event_id: str) -> bool: ...

    async def mark_processed(self, tenant_id: str, event_id: str) -> None: ...

    async def claim(self, tenant_id: str, event_id: str) -> bool: ...


@runtime_checkable
class Notifier(Protocol):
    async def notify(self, *, tenant_id: str, recipient: str, kind: str, detail: dict) -> None: ...


@dataclass
class ServiceDeps:
    settings: Any
    clock: Any
    uow_factory: Any
    registry: ModelRegistry
    executor: ScoringExecutor
    dedup: Dedup
    notifier: Notifier | None = None
    budget_gate: Any = None
