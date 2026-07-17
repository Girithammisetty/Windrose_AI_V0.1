"""Ports (interfaces) between the domain and adapters/stores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.entities import (
    ChartRef,
    CompileLogEntry,
    ModelVersion,
    Operation,
    SemanticModel,
    VerifiedQuery,
)


@dataclass(slots=True)
class Page:
    items: list[Any]
    next_cursor: str | None
    has_more: bool


class ModelRepo(Protocol):
    async def add(self, model: SemanticModel) -> None: ...
    async def get(self, model_id: str, include_deleted: bool = False) -> SemanticModel | None: ...
    async def get_by_name(self, workspace_id: str, name: str) -> SemanticModel | None: ...
    async def update(self, model: SemanticModel) -> None: ...
    async def list(self, workspace_id: str | None, limit: int, cursor: str | None) -> Page: ...
    async def all_active(self) -> list[SemanticModel]: ...


class VersionRepo(Protocol):
    async def add(self, version: ModelVersion) -> None: ...
    async def get(self, model_id: str, version_no: int) -> ModelVersion | None: ...
    async def get_by_id(self, version_id: str) -> ModelVersion | None: ...
    async def latest(self, model_id: str) -> ModelVersion | None: ...
    async def open_version(self, model_id: str) -> ModelVersion | None:
        """The draft/in_review/rejected version, if any (at most one open)."""
        ...

    async def list(self, model_id: str, limit: int, cursor: str | None) -> Page: ...
    async def update(self, version: ModelVersion) -> None: ...
    async def lock_model(self, model_id: str) -> None:
        """Per-model advisory lock serializing publication (BR-10)."""
        ...

    async def rebuild_projections(self, version: ModelVersion) -> None:
        """Rebuild normalized entity/dimension/measure/join rows (BRD §4.1)."""
        ...


class VerifiedQueryRepo(Protocol):
    async def add(self, vq: VerifiedQuery) -> None: ...
    async def get(self, vq_id: str) -> VerifiedQuery | None: ...
    async def update(self, vq: VerifiedQuery) -> None: ...
    async def list(self, workspace_id: str | None, status: str | None,
                   limit: int, cursor: str | None) -> Page: ...
    async def search(self, workspace_id: str, embedding: list[float],
                     top_k: int) -> list[tuple[VerifiedQuery, float]]:
        """ANN over APPROVED entries; tenant+workspace filtered in SQL (BR-14)."""
        ...

    async def approved_for_model(self, model_id: str) -> list[VerifiedQuery]: ...
    async def approved_all(self) -> list[VerifiedQuery]: ...


class CompileLogRepo(Protocol):
    async def add(self, entry: CompileLogEntry) -> None: ...


class OperationRepo(Protocol):
    async def add(self, op: Operation) -> None: ...
    async def get(self, op_id: str) -> Operation | None: ...
    async def update(self, op: Operation) -> None: ...


class ChartRefRepo(Protocol):
    async def upsert(self, ref: ChartRef) -> None: ...
    async def charts_referencing(self, measure: str) -> list[ChartRef]: ...


class OutboxRepo(Protocol):
    async def add(self, topic: str, envelope: dict) -> None: ...


class IdempotencyRepo(Protocol):
    async def get(self, key: str) -> dict | None: ...
    async def put(self, key: str, request_hash: str, status_code: int, body: dict) -> None: ...


class UnitOfWork(Protocol):
    """Tenant-scoped unit of work. SQL impl binds RLS via `app.tenant_id`."""

    tenant_id: str
    models: ModelRepo
    versions: VersionRepo
    verified_queries: VerifiedQueryRepo
    compile_log: CompileLogRepo
    operations: OperationRepo
    chart_refs: ChartRefRepo
    outbox: OutboxRepo
    idempotency: IdempotencyRepo

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UowFactory(Protocol):
    def __call__(self, tenant_id: str) -> UnitOfWork: ...


# ---------------------------------------------------------------------------
# Adapters


class DatasetClient(Protocol):
    """dataset-service facade: entity binding validation + sample values."""

    async def get_dataset(self, tenant_id: str, dataset_urn: str) -> dict | None:
        """{"exists": bool, "table": str, "schema": {col: type},
            "top_values": {col: [...]}} or None."""
        ...


class QueryServiceClient(Protocol):
    """query-service facade: dry-run validation for compile?validate=true.
    ``token`` is the caller's bearer JWT, forwarded to query-service's
    JWT-authenticated /api/v1/sql/dry-run (query-service has no internal/
    SPIFFE route)."""

    async def dry_run(self, tenant_id: str, sql: str, params: list[dict],
                      dialect: str, token: str) -> dict:
        """{"valid": bool, "estimated_bytes": int|None, "verdict": "ok"|"over_ceiling",
            "message": str|None}."""
        ...


class EmbeddingClient(Protocol):
    """ai-gateway embeddings for verified-query semantic search."""

    async def embed(self, tenant_id: str, text: str) -> list[float]: ...


class EventBus(Protocol):
    async def publish(self, topic: str, envelope: dict) -> None: ...


class DedupStore(Protocol):
    async def seen(self, tenant_id: str, event_id: str) -> bool: ...
