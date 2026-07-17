from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# Reserved platform tenant uuid (platform-scoped rows: providers, platform
# ladders, platform/system budgets).
PLATFORM_TENANT_ID = "00000000-0000-7000-8000-000000000001"

REQUEST_CLASSES = ("chat", "sql-gen", "judge", "embed")

# Default model ladders per request class (AIG-FR-005).
DEFAULT_LADDERS: dict[str, list[dict]] = {
    "chat": [
        {"model_alias": "fast-small", "max_tokens": 4096, "temperature_default": 0.7, "cost_tier": 1},
        {"model_alias": "balanced", "max_tokens": 8192, "temperature_default": 0.7, "cost_tier": 2},
        {"model_alias": "frontier", "max_tokens": 16384, "temperature_default": 0.7, "cost_tier": 3},
    ],
    "sql-gen": [
        {"model_alias": "fast-small", "max_tokens": 4096, "temperature_default": 0.1, "cost_tier": 1},
        {"model_alias": "balanced", "max_tokens": 8192, "temperature_default": 0.1, "cost_tier": 2},
        {"model_alias": "frontier", "max_tokens": 16384, "temperature_default": 0.1, "cost_tier": 3},
    ],
    "judge": [
        {"model_alias": "balanced", "max_tokens": 8192, "temperature_default": 0.0, "cost_tier": 2},
        {"model_alias": "frontier", "max_tokens": 16384, "temperature_default": 0.0, "cost_tier": 3},
    ],
    "embed": [
        {"model_alias": "embed-standard", "max_tokens": 8192, "temperature_default": 0.0, "cost_tier": 1},
    ],
}

# Versioned price table (BR-5): USD per 1K tokens per model ALIAS. This is the
# fallback tier used when no exact (provider, model_id) price is published.
DEFAULT_PRICE_TABLE: dict[str, dict[str, float]] = {
    "fast-small": {"input_per_1k": 0.00025, "output_per_1k": 0.00125},
    "balanced": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    "frontier": {"input_per_1k": 0.015, "output_per_1k": 0.075},
    "embed-standard": {"input_per_1k": 0.0001, "output_per_1k": 0.0},
}

# Cost-detail: accurate price PER (provider, model_id), USD per 1K tokens. Seeded
# from REAL published per-1M list prices (converted /1000). This is the precise
# tier the settle path prefers over the alias fallback above, keyed on the
# concrete provider + provider-side model id the request actually ran on.
#   Anthropic (per 1M in/out -> per 1K): Opus 4.8 $5/$25, Sonnet 5 $3/$15,
#   Haiku 4.5 $1/$5, Opus 4.7 $5/$25, Fable 5 $10/$50.
# Ollama/local models are $0/$0 (handled as a provider-level zero in quote_for,
# so any local model id is free without enumerating them).
# OpenAI/Azure OpenAI model prices are intentionally NOT invented here — set the
# price per deployment (secret/config) or extend this table with real published
# values; unlisted (provider, model) pairs fall back to the alias table above.
DEFAULT_PROVIDER_PRICE_TABLE: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": {
        "claude-opus-4-8": {"input_per_1k": 0.005, "output_per_1k": 0.025},
        "claude-sonnet-5": {"input_per_1k": 0.003, "output_per_1k": 0.015},
        "claude-haiku-4-5": {"input_per_1k": 0.001, "output_per_1k": 0.005},
        "claude-opus-4-7": {"input_per_1k": 0.005, "output_per_1k": 0.025},
        "claude-fable-5": {"input_per_1k": 0.010, "output_per_1k": 0.050},
    },
}

DEFAULT_GUARDRAIL_POLICY: dict = {
    "pii": {
        "mode": "redact",  # redact | block | off
        "entities": ["EMAIL", "PHONE", "CREDIT_CARD", "SSN", "IBAN"],
        "deredact_response": False,
    },
    "injection": {"mode": "block", "flag_threshold": 0.65, "block_threshold": 0.85},
    "schema_validation": "on",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIG_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "ai-gateway"

    database_url: str = "postgresql+asyncpg://aigw:aigw@localhost:5432/ai_gateway"
    redis_url: str = "redis://localhost:6379/0"

    # Runtime adapter selection (CONVENTIONS.md END STATE). The RUNTIME DEFAULT
    # is True: main.py wires the real shared-infra adapters — Ollama LLM
    # provider, the windrose_common Kafka producer (Redpanda), the OPA sidecar
    # and Redis — over a Postgres-backed (RLS) store. The unit tier explicitly
    # sets this False (tests/conftest) so the in-process/in-memory doubles are
    # reachable ONLY from tests. Set AIG_USE_REAL_ADAPTERS=false to run fully
    # self-contained.
    use_real_adapters: bool = True

    # Real local LLM (Ollama, OpenAI-compatible API). Chat/completions use the
    # chat models; /v1/embeddings uses the embedding model. Deployment rows map a
    # ladder rung alias (model_family) to a concrete Ollama model (deployment_name).
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_request_timeout_s: float = 120.0

    # Azure OpenAI api-version query param default (used by the OpenAI-compatible
    # adapter when a deployment's provider is azure_openai and the secret carries
    # no api_version). Overridable per-secret.
    azure_openai_api_version: str = "2024-06-01"

    # Real event bus (Redpanda / Kafka API) + OPA sidecar.
    kafka_bootstrap_servers: str = "localhost:9092"
    opa_url: str = "http://localhost:8281"

    # AuthN (MASTER-FR-010/011). In dev/tests a static PEM is used; prod uses JWKS.
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = None
    jwks_ttl_seconds: int = 300

    # Internal (service-to-service) auth: SPIFFE identity forwarded by the mesh
    # sidecar after mTLS termination (MASTER-FR-014). agent-runtime mints
    # per-run virtual keys through this path (AIG-FR-032).
    spiffe_header: str = "x-client-spiffe-id"
    internal_allowed_spiffe: list[str] = [
        "spiffe://windrose/ns/ai/sa/agent-runtime",
        "spiffe://windrose/ns/ai/sa/eval-service",
    ]

    platform_tenant_id: str = PLATFORM_TENANT_ID

    # Deploy-time action-catalog registration (RBC-FR-022) — mirrors
    # eval-service's app/registration.py. Without this, rbac's projector never
    # learns these actions exist, so OPA denies every principal for every
    # ai-gateway admin route regardless of role grants (action_known=false).
    rbac_url: str | None = None
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None

    # Budgets (AIG-FR-020..025, BR-12)
    default_tenant_budget_daily_usd: float = 150.0
    default_tenant_budget_monthly_usd: float = 2000.0
    system_budget_daily_usd: float = 1000.0
    system_budget_monthly_usd: float = 20000.0
    default_degrade_pct: int = 95
    reservation_ttl_seconds: int = 180

    # Pricing (BR-5)
    price_version: str = "2026-07-01"

    # Admission (AIG-FR-011, BR-13)
    streams_cap_per_tenant: int = 50
    rpm_cap_per_tenant: int = 600
    tpm_cap_per_tenant: int = 200_000

    # Retry/failover (AIG-FR-008)
    retry_backoff_min_ms: int = 250
    retry_backoff_max_ms: int = 1000
    connect_timeout_s: float = 5.0
    first_token_timeout_s: float = 30.0
    total_timeout_s: float = 120.0

    # Circuit breaker (AIG-FR-009) + health probes (AIG-FR-009a)
    breaker_consecutive_failures: int = 5
    breaker_error_rate_threshold: float = 0.5
    breaker_window_seconds: int = 60
    breaker_halfopen_after_seconds: int = 30
    probe_failure_threshold: int = 3

    # Semantic cache (AIG-FR-040..043, BR-15)
    cache_ttl_seconds_default: int = 86_400
    cache_ttl_seconds_max: int = 7 * 86_400
    cache_similarity_threshold: float = 0.97
    cache_similarity_floor: float = 0.95
    cache_min_prompt_tokens: int = 64
    cache_max_temperature: float = 0.2
    embedding_dim: int = 1536

    # Embedding batches (BR-17)
    embed_batch_max_inputs: int = 256

    # Reconciliation (AC-16)
    reconciliation_drift_alert_pct: float = 1.0

    # Anomaly detection (AIG-FR-025)
    anomaly_multiplier: float = 3.0

    events_topic: str = "ai.events.v1"
    usage_topic: str = "ai.token_usage.v1"
