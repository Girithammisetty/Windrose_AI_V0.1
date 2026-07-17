"""Domain entities (BRD 12 §4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

PROVIDERS = ("azure_openai", "bedrock", "vertex", "anthropic", "ollama")
CLOUDS = ("aws", "azure", "gcp")
DEPLOYMENT_STATUSES = ("active", "draining", "disabled")
SCOPE_TYPES = ("platform", "tenant", "workspace", "principal", "virtual_key")
WINDOWS = ("daily", "monthly")

# Reserved scope_ref for the platform system budget that judge/guardrail
# internals draw from (AIG-FR-023 / BR-7).
SYSTEM_SCOPE_REF = "system"


@dataclass
class ProviderDeployment:
    id: str
    tenant_id: str
    provider: str
    model_family: str  # the model_alias this deployment serves (ladder rung alias)
    deployment_name: str
    region: str
    cloud: str
    endpoint_vault_ref: str
    tpm_limit: int
    rpm_limit: int
    priority: int  # lower = preferred
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass
class Rung:
    model_alias: str
    max_tokens: int
    temperature_default: float
    cost_tier: int

    @classmethod
    def from_dict(cls, d: dict) -> Rung:
        return cls(
            model_alias=d["model_alias"],
            max_tokens=int(d["max_tokens"]),
            temperature_default=float(d["temperature_default"]),
            cost_tier=int(d["cost_tier"]),
        )

    def to_dict(self) -> dict:
        return {
            "model_alias": self.model_alias,
            "max_tokens": self.max_tokens,
            "temperature_default": self.temperature_default,
            "cost_tier": self.cost_tier,
        }


@dataclass
class ModelLadder:
    id: str
    tenant_id: str
    request_class: str  # chat | sql-gen | judge | embed
    scope: str  # platform | tenant
    rungs: list[dict]  # ordered rung array (documented JSONB, ≤8KB)
    version: int = 1
    max_rung: int | None = None  # tenant rung cap (AIG-FR-006)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    def rung(self, index: int) -> Rung:
        return Rung.from_dict(self.rungs[index])

    @property
    def top_rung(self) -> int:
        return len(self.rungs) - 1


@dataclass
class Budget:
    id: str
    tenant_id: str
    scope_type: str
    scope_ref: str
    window: str  # daily | monthly
    limit_usd: float
    degrade_pct: int = 95
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def limit_cents(self) -> int:
        return int(round(self.limit_usd * 100))


@dataclass
class VirtualKey:
    id: str
    tenant_id: str
    key_hash: str
    principal_type: str  # user | agent | service
    principal_id: str
    allowed_request_classes: list[str]
    max_rung: int
    expires_at: datetime | None = None
    status: str = "active"  # active | revoked
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass
class GuardrailPolicy:
    id: str
    tenant_id: str
    policy: dict  # documented JSONB, ≤8KB (AIG-FR-053)
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass
class CacheEntry:
    id: str
    tenant_id: str
    prompt_hash: str
    context_hash: str
    embedding: list[float] | None
    response: dict
    workspace_id: str | None
    expires_at: datetime
    created_at: datetime | None = None


@dataclass
class RequestLog:
    request_id: str
    tenant_id: str
    principal: str
    request_class: str
    model_alias: str
    rung: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cached: bool
    guardrail_flags: list[str]
    status: str  # ok | <ERROR_CODE>
    latency_ms: int
    trace_id: str | None
    deployment_id: str | None = None
    created_at: datetime | None = None


@dataclass
class TenantConfig:
    """Projected from identity.events.v1 (timezone for BR-4 windows, cell
    cloud fallback, cache TTL override)."""

    tenant_id: str
    timezone: str = "UTC"
    cell_cloud: str | None = None
    cache_ttl_seconds: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Attribution:
    """Attribution tags (AIG-FR-002); tenant comes from the JWT only."""

    workspace_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    agent_version: str | None = None
    tool: str | None = None
    feature: str | None = None


@dataclass
class GoverningWindow:
    budget: Budget
    window_start: str  # ISO date
    ledger_key: str
    reset_at: datetime


@dataclass
class Reservation:
    governing: GoverningWindow
    reservation_id: str
    amount_cents: int


@dataclass
class RoutingAttempt:
    deployment_id: str
    provider: str
    outcome: str  # ok | error | timeout | retry


@dataclass
class PipelineResult:
    """Everything the API layer needs to shape the response."""

    request_id: str
    response: dict | None
    rung: int
    model_alias: str
    deployment_id: str | None
    cache: str  # hit_exact | hit_semantic | miss | skip
    degraded: bool
    escalated: bool
    guardrail_flags: list[str] = field(default_factory=list)
    stream: object | None = None  # async generator of SSE lines when streaming
