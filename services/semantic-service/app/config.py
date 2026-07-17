from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEM_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "semantic-service"

    database_url: str = "postgresql+asyncpg://semantic:semantic@localhost:5432/semantic"

    # AuthN (MASTER-FR-010/011). In dev/tests a static PEM is used; prod uses JWKS.
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = None
    jwks_ttl_seconds: int = 300

    # Internal (service-to-service) auth: SPIFFE identity forwarded by the mesh
    # sidecar after mTLS termination (MASTER-FR-014).
    spiffe_header: str = "x-client-spiffe-id"
    internal_allowed_spiffe: list[str] = [
        "spiffe://windrose/ns/viz/sa/chart-service",
        "spiffe://windrose/ns/data/sa/query-service",
        "spiffe://windrose/ns/ai/sa/agent-runtime",
    ]
    # semantic-service's own SPIFFE identity, forwarded on outbound internal
    # (service-to-service) calls to dataset-service.
    service_spiffe_id: str = "spiffe://windrose/ns/data/sa/semantic-service"

    events_topic: str = "semantic.events.v1"

    # Adapter selection (CONVENTIONS.md END STATE). The RUNTIME DEFAULT is True:
    # `app.main:app` / `make run` / the boot script wire the REAL adapters —
    # Redpanda (Kafka), Redis, OPA, Ollama embeddings and the sibling HTTP
    # services — against a Postgres-backed (RLS) store. The unit tier explicitly
    # sets this False (tests/conftest) so the local doubles (StaticDatasetClient,
    # FakeQueryServiceClient, LocalHashEmbedding, in-memory bus/dedup,
    # LocalScopeAuthz) are reachable ONLY from tests. Set
    # SEM_USE_REAL_ADAPTERS=false to run fully self-contained.
    use_real_adapters: bool = True

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

    # Sibling services (service-to-service HTTP). dataset-service is called over
    # SPIFFE mTLS in the mesh; query-service has no internal/SPIFFE route, so its
    # dry-run call forwards the caller's own bearer JWT instead (matches how
    # chart-service and case-service call query-service).
    dataset_service_url: str = "http://localhost:8083"
    query_service_url: str = "http://localhost:8085"
    http_timeout_s: float = 5.0

    # Embeddings (SEM-FR-041). The platform embeds via ai-gateway, which serves a
    # real model; for a self-contained local runtime the embeddings adapter points
    # at Ollama's OpenAI-compatible endpoint serving the real nomic-embed-text
    # model (768-dim). Both are real /v1/embeddings servers — swap the base URL.
    embeddings_base_url: str = "http://localhost:11434/v1"
    embeddings_model: str = "nomic-embed-text"
    embeddings_api_key: str | None = None

    # Compile limits (SEM-FR-022f, §9 NFR deltas)
    compile_limit_cap: int = 50_000
    compile_max_dimensions: int = 8
    compile_max_metrics: int = 20
    agent_limit_cap: int = 10_000  # ceiling applied to MCP compile_metric_sql

    # Definition size limits (§9)
    definition_max_bytes: int = 256 * 1024
    max_entities: int = 100
    max_dimensions: int = 500
    max_measures: int = 500
    max_join_paths: int = 200

    # BR-5: relative time ranges resolve in the tenant's reporting timezone
    # (workspace setting; default UTC).
    reporting_timezone: str = "UTC"

    # Verified queries. 768 = nomic-embed-text dimensionality (the real local
    # embedding model); the pgvector column and HNSW index are vector(768).
    embedding_dim: int = 768
    search_top_k_max: int = 10
