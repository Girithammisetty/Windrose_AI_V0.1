"""Ports (Protocol interfaces) between domain logic and adapters/stores."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.domain.entities import (
    Budget,
    CacheEntry,
    GuardrailPolicy,
    ModelLadder,
    ProviderDeployment,
    RequestLog,
    TenantConfig,
    VirtualKey,
)


@dataclass
class Page:
    data: list
    next_cursor: str | None = None
    has_more: bool = False


# --------------------------------------------------------------------------- repos


class ProviderRepo(Protocol):
    async def add(self, d: ProviderDeployment) -> None: ...
    async def get(self, deployment_id: str) -> ProviderDeployment | None: ...
    async def update(self, d: ProviderDeployment) -> None: ...
    async def list(self, limit: int, cursor: str | None) -> Page: ...
    async def list_all_active_or_draining(self) -> list[ProviderDeployment]: ...
    async def count_active_for_alias(self, model_alias: str,
                                     exclude_id: str | None = None) -> int: ...


class LadderRepo(Protocol):
    async def get(self, request_class: str, scope: str) -> ModelLadder | None: ...
    async def upsert(self, ladder: ModelLadder) -> ModelLadder: ...


class BudgetRepo(Protocol):
    async def add(self, b: Budget) -> None: ...
    async def get(self, budget_id: str) -> Budget | None: ...
    async def update(self, b: Budget) -> None: ...
    async def list(self, limit: int, cursor: str | None,
                   scope_type: str | None = None) -> Page: ...
    async def for_scope(self, scope_type: str, scope_ref: str) -> list[Budget]: ...


class KeyRepo(Protocol):
    async def add(self, k: VirtualKey) -> None: ...
    async def get(self, key_id: str) -> VirtualKey | None: ...
    async def get_by_hash_any_tenant(self, key_hash: str) -> VirtualKey | None: ...
    async def update(self, k: VirtualKey) -> None: ...
    async def list(self, limit: int, cursor: str | None) -> Page: ...
    async def list_active(self) -> list[VirtualKey]: ...


class PolicyRepo(Protocol):
    async def current(self) -> GuardrailPolicy | None: ...
    async def put(self, policy: GuardrailPolicy) -> GuardrailPolicy: ...


class RequestLogRepo(Protocol):
    async def add(self, entry: RequestLog) -> None: ...
    async def get(self, request_id: str) -> RequestLog | None: ...
    async def aggregate_costs(self, since: datetime) -> list[dict]:
        """Cost-detail aggregation over the tenant's request_log since `since`,
        grouped by (deployment_id, model_alias, request_class, cached). Rows:
        {deployment_id, model_alias, request_class, cached, requests,
        input_tokens, output_tokens, cost_usd}. Provider + concrete model id are
        resolved from deployment_id by the admin layer (RLS keeps deployment rows
        in the platform tenant, so the join is done in Python, not SQL)."""
        ...


class CacheEntryRepo(Protocol):
    async def add(self, entry: CacheEntry) -> None: ...
    async def search(self, context_hash: str, embedding: list[float],
                     threshold: float, now: datetime) -> CacheEntry | None: ...
    async def purge(self, workspace_id: str | None = None) -> int: ...


class TenantConfigRepo(Protocol):
    async def get(self, tenant_id: str) -> TenantConfig | None: ...
    async def put(self, cfg: TenantConfig) -> None: ...


class OutboxRepo(Protocol):
    async def add(self, topic: str, envelope: dict) -> None: ...


class IdempotencyRepo(Protocol):
    async def get(self, key: str) -> dict | None: ...
    async def put(self, key: str, request_hash: str, status_code: int, body: dict) -> None: ...


class UnitOfWork(Protocol):
    tenant_id: str
    providers: ProviderRepo
    ladders: LadderRepo
    budgets: BudgetRepo
    keys: KeyRepo
    policies: PolicyRepo
    request_log: RequestLogRepo
    cache_entries: CacheEntryRepo
    tenant_configs: TenantConfigRepo
    outbox: OutboxRepo
    idempotency: IdempotencyRepo

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


UowFactory = Callable[[str], UnitOfWork]


# --------------------------------------------------------------------------- ledger


class LedgerUnavailable(Exception):
    """The ledger backend is down; callers decide fallback/fail-closed (BR-14)."""


class LedgerStore(Protocol):
    """Atomic per-window budget counters (spent + reservations, cents)."""

    async def reserve(self, key: str, limit_cents: int, amount_cents: int,
                      reservation_id: str) -> bool: ...
    async def settle(self, key: str, reservation_id: str,
                     actual_cents: int) -> tuple[int, int]:
        """Returns (prev_spent_cents, new_spent_cents)."""
        ...
    async def release(self, key: str, reservation_id: str) -> None: ...
    async def usage(self, key: str) -> tuple[int, int]:
        """Returns (spent_cents, reserved_cents)."""
        ...
    async def flag_once(self, flag_key: str) -> bool: ...
    async def sweep_expired(self) -> int: ...


# --------------------------------------------------------------------------- kv


class KV(Protocol):
    """Small hot-state store (exact cache tier, admission counters)."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def incr(self, key: str, ttl_seconds: int | None = None) -> int: ...
    async def incrby(self, key: str, amount: int, ttl_seconds: int | None = None) -> int: ...
    async def decr(self, key: str) -> int: ...
    async def setnx(self, key: str, value: str, ttl_seconds: int | None = None) -> bool: ...


class InvalidationChannel(Protocol):
    """`keyrev` pub/sub: key/policy invalidation across replicas (AIG-FR-031)."""

    async def publish(self, kind: str, ref: str) -> None: ...
    def subscribe(self, callback: Callable[[str, str], Awaitable[None]]) -> None: ...


# --------------------------------------------------------------------------- providers


@dataclass
class ProviderRequest:
    model: str  # provider-side deployment/model name
    messages: list[dict]
    max_tokens: int
    temperature: float
    stream: bool = False
    response_format: dict | None = None
    tools: list[dict] | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class ProviderResult:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    finish_reason: str = "stop"


class ProviderError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(message or f"provider error {status}")
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status == 429 or self.status >= 500


class ProviderTimeout(ProviderError):
    def __init__(self, message: str = "provider timeout"):
        super().__init__(status=599, message=message)


class ProviderNotConfigured(ProviderError):
    """A deployment names a provider type whose execution path is accepted by
    the admin/config layer but cannot run in this deployment because the real
    cloud-credential wiring is absent (bedrock SigV4 / vertex ADC) or the
    per-deployment credential could not be resolved. This is an HONEST,
    non-retryable failure surfaced to the admin — never a silent fake success
    (Rule 2). Status 501 keeps it out of the retry/failover path."""

    def __init__(self, message: str = "provider not configured"):
        super().__init__(status=501, message=message)

    @property
    def retryable(self) -> bool:  # never retry/failover a config gap
        return False


@dataclass
class ProviderCredential:
    """Per-deployment endpoint + secret resolved from the gateway's OWN
    credential store (never vendor-SDK env auto-resolution). `base_url` is the
    provider API root; `api_key` is the bearer/x-api-key/api-key secret."""

    base_url: str | None
    api_key: str | None = None
    api_version: str | None = None  # azure_openai `api-version` query param


class ProviderClient(Protocol):
    async def complete(self, deployment: ProviderDeployment,
                       request: ProviderRequest) -> ProviderResult: ...
    def stream(self, deployment: ProviderDeployment,
               request: ProviderRequest) -> AsyncIterator[dict]: ...
    async def embed(self, deployment: ProviderDeployment, model: str,
                    inputs: list[str]) -> tuple[list[list[float]], int]:
        """Returns (vectors, input_tokens)."""
        ...


# --------------------------------------------------------------------------- guardrail adapters


@dataclass
class PIIEntity:
    kind: str
    start: int
    end: int
    text: str


class PIIAnalyzer(Protocol):
    def analyze(self, text: str, entities: list[str]) -> list[PIIEntity]: ...


class InjectionClassifier(Protocol):
    def score(self, text: str) -> float: ...


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


# --------------------------------------------------------------------------- observability


class Span:
    """Minimal span record; OTel exporter adapter is a prod stub (wave-1)."""

    def __init__(self, name: str):
        self.name = name
        self.attributes: dict[str, Any] = {}
        self.events: list[dict] = []

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        self.events.append({"name": name, "attributes": attributes or {}})


class Tracer:
    """In-memory tracer implementing the span-attribute contract; the OTel
    SDK/Langfuse exporter is a production adapter (TODO, MASTER-FR-050/052)."""

    def __init__(self):
        self.spans: list[Span] = []

    def start_span(self, name: str) -> Span:
        span = Span(name)
        self.spans.append(span)
        return span

    def spans_named(self, name: str) -> list[Span]:
        return [s for s in self.spans if s.name == name]


class Metrics:
    """Counter/gauge registry rendered at /metrics (AIG-FR-062)."""

    def __init__(self):
        self.counters: dict[tuple[str, tuple], float] = {}
        self.gauges: dict[tuple[str, tuple], float] = {}

    def inc(self, name: str, value: float = 1.0, **labels) -> None:
        key = (name, tuple(sorted(labels.items())))
        self.counters[key] = self.counters.get(key, 0.0) + value

    def gauge(self, name: str, value: float, **labels) -> None:
        self.gauges[(name, tuple(sorted(labels.items())))] = value

    def render(self) -> str:
        lines = []
        for (name, labels), value in sorted({**self.counters, **self.gauges}.items()):
            label_str = ",".join(f'{k}="{v}"' for k, v in labels)
            lines.append(f"{name}{{{label_str}}} {value}")
        return "\n".join(lines) + "\n"
