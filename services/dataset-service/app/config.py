from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DST_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "dataset-service"

    database_url: str = "postgresql+asyncpg://dataset:dataset@localhost:5432/dataset"

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
        "spiffe://windrose/ns/data/sa/profiler",
        "spiffe://windrose/ns/data/sa/ingestion-service",
        "spiffe://windrose/ns/data/sa/pipeline-orchestrator",
        "spiffe://windrose/ns/data/sa/inference-service",
        "spiffe://windrose/ns/data/sa/semantic-service",
        # BRD 56 inc2: the tool-plane MCP gateway federates approved
        # dataset.entity.merge proposals to the /internal/v1/mcp/invoke facade.
        "spiffe://windrose/ns/tools/sa/mcp-gateway",
    ]

    # Adapter selection (CONVENTIONS.md END STATE). The RUNTIME DEFAULT is True:
    # `app.main:app` wires the shared windrose_common adapters against local
    # infra (MinIO, Iceberg REST, Redpanda, OPA, Redis) over a Postgres-backed
    # store. The unit tier explicitly sets this False (tests/conftest) so the
    # local doubles (LocalCatalog, LocalFSObjectStore, in-memory bus/dedup) are
    # reachable ONLY from tests. Set DST_USE_REAL_ADAPTERS=false to run fully
    # self-contained.
    use_real_adapters: bool = True

    # Swappable dependency provider selection (Phase 3). When unset each is
    # derived from `use_real_adapters` (backward compatible); set explicitly to
    # MIX backends, e.g. catalog_provider=local + object_store_provider=s3.
    #   catalog_provider:      local | iceberg_rest
    #   object_store_provider: local | s3
    catalog_provider: str | None = None
    object_store_provider: str | None = None

    # Local adapters (unit tier)
    object_store_dir: str = "/tmp/windrose/dataset-service/objects"
    catalog_dir: str = "/tmp/windrose/dataset-service/catalog"
    signed_url_ttl_hours: int = 24

    # Real-adapter endpoints (deploy/docker-compose.dev.yml defaults)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "windrose"
    s3_secret_key: str = "windrose_dev"
    s3_region: str = "us-east-1"
    profiles_bucket: str = "windrose-profiles"
    iceberg_catalog_uri: str = "http://localhost:8181"
    iceberg_warehouse: str = "s3://windrose-warehouse/"
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    opa_url: str = "http://localhost:8281"

    # Lineage (DST-FR-042)
    lineage_default_depth: int = 3
    lineage_max_depth: int = 10
    lineage_node_cap: int = 1000

    # Profiling (DST-FR-020..025)
    profiler_version: str = "windrose-profiler/0.1.0-inproc"
    profile_timeout_minutes: int = 30
    profile_retrigger_per_hour: int = 3
    profile_sample_max_rows: int = 10_000_000

    # Retention (DST-FR-080/081) & restore (DST-FR-006)
    retention_keep_all_days: int = 90
    retention_keep_last: int = 10
    retention_monthly_months: int = 13
    retention_trained_pin_days: int = 400
    restore_window_days: int = 30

    events_topic: str = "dataset.events.v1"

    # Deploy-time action-catalog registration (RBC-FR-022). The service pushes
    # its action manifest to rbac at startup so OPA's catalog knows each action.
    rbac_url: str | None = None
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None
