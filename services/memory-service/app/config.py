from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEM_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "memory-service"

    # App DB connection (non-privileged `memory_app` role in prod; RLS applies).
    database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/memory"
    # Privileged connection used ONLY for control-plane DDL: per-tenant schema
    # provisioning + drop (needs CREATE SCHEMA). Defaults to the owner role.
    admin_database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/memory"

    # AuthN (MASTER-FR-010/011). Prod verifies via identity-service JWKS (default
    # below); set MEM_JWT_PUBLIC_KEY_PEM to a static PEM for dev/tests or an
    # air-gapped probe (it takes precedence over JWKS when present).
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = "http://localhost:8082/.well-known/jwks.json"
    jwks_ttl_seconds: int = 300

    # Internal (service-to-service) mTLS SPIFFE allowlist (MASTER-FR-014).
    spiffe_header: str = "x-client-spiffe-id"
    internal_allowed_spiffe: list[str] = [
        "spiffe://windrose/ns/ai/sa/agent-runtime",
        "spiffe://windrose/ns/ai/sa/eval-service",
    ]

    # Adapter selection (CONVENTIONS.md END STATE). The RUNTIME DEFAULT is True:
    # `app.main:app` / `make run` / the Docker entrypoint wire the REAL adapters
    # against local infra — Postgres+pgvector (schema-per-tenant store), Ollama
    # embeddings, Redpanda (Kafka bus+consumers), Redis (dedup/session/membership)
    # and OPA — with the store in "sql" mode. The unit tier explicitly sets this
    # False (tests/conftest.make_settings) so the in-memory doubles are reachable
    # ONLY from tests. Set MEM_USE_REAL_ADAPTERS=false to run fully self-contained.
    use_real_adapters: bool = True

    # Store backend: "sql" (Postgres+pgvector, runtime default) or "memory"
    # (in-memory double, tests only). None => derived from use_real_adapters.
    store_mode: str | None = None

    # Real-adapter endpoints (deploy/docker-compose.dev.yml defaults)
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    opa_url: str = "http://localhost:8281"

    # Deploy-time action-catalog registration (RBC-FR-022): POST the manifest
    # to rbac /api/v1/actions/register at startup, signed with the service key.
    # Skipped (with a log line) when rbac_url / the signing key are unset.
    rbac_url: str | None = None
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None

    events_topic: str = "memory.events.v1"

    # Embeddings (MEM-FR-002). Real local model = Ollama nomic-embed-text (768).
    embeddings_base_url: str = "http://localhost:11434/v1"
    embeddings_model: str = "nomic-embed-text"
    embeddings_api_key: str | None = None
    embedding_dim: int = 768
    active_embedding_ver: str = "nomic-embed-text/v1"

    # Write path (MEM-FR-010)
    content_max_bytes: int = 8 * 1024
    max_tags: int = 16
    dedup_threshold: float = 0.92
    injection_block_threshold: float = 0.7
    batch_max: int = 50

    # Scope caps (MEM-FR-004)
    cap_user: int = 2000
    cap_workspace: int = 10000
    cap_tenant: int = 20000
    cap_eviction_skip_days: int = 7

    # TTL platform bounds in days (MEM-FR-003)
    ttl_user_default_days: int = 180
    ttl_user_max_days: int = 400
    ttl_workspace_default_days: int = 365
    ttl_tenant_default_days: int = 730

    # Recency half-lives in days for ranking (BR-16)
    half_life_user_days: int = 30
    half_life_workspace_days: int = 90
    half_life_tenant_days: int = 180

    # Ranking weights (MEM-FR-022)
    w_sim: float = 0.65
    w_rec: float = 0.20
    w_conf: float = 0.15
    default_conf_for_chunk: float = 0.5

    # Retrieval (MEM-FR-020/023)
    retrieve_top_k_max: int = 24

    # Confidence defaults (MEM-FR-013)
    conf_user_explicit: float = 0.95
    conf_agent_run: float = 0.7
    conf_tool_output: float = 0.6
    conf_retrieval_bump: float = 0.02
    conf_cap: float = 0.99

    # Re-validation (MEM-FR-042)
    revalidate_fraction: float = 0.5
    revalidate_decay: float = 0.15
    revalidate_expire_below: float = 0.3
    expire_grace_days: int = 30
    quarantine_purge_days: int = 90

    # Sessions (MEM-FR-002 / BR-3). Redis TTL = session lifetime + 1h.
    session_ttl_seconds: int = 8 * 3600 + 3600

    # Embedding-outage write queue (BR-2 / AC-11): screened+PII-checked writes
    # wait in `mem:pend` for the embeddings backend to recover; after this window
    # the entry fails (never persisted unembedded).
    pending_window_seconds: int = 3600

    # Corpora (MEM-FR-030..032)
    chunk_max_tokens: int = 400
    chunk_overlap: int = 40
    chunk_content_max_bytes: int = 2 * 1024
    chunk_cap_per_source: int = 2000
    snapshot_retention_days: int = 35

    # Erasure (MEM-FR-040). Temporal drives it in prod; the in-process
    # orchestrator runs the SAME idempotent activities against real stores.
    erasure_sla_hours: int = 24
