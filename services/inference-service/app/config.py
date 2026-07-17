from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INF_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "inference-service"

    # The runtime connects as the NON-superuser, non-owner ``inference_app`` role
    # (created by the migration) so Postgres RLS (ENABLE+FORCE) actually enforces
    # tenant isolation on the running service. Migrations run as a privileged role
    # via INF_MIGRATE_URL; the service must never connect as a superuser/owner.
    database_url: str = (
        "postgresql+asyncpg://inference_app:inference_app@localhost:5432/windrose"
    )

    # AuthN (MASTER-FR-010/011). In dev/tests a static PEM is used; prod uses JWKS.
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = None
    jwks_ttl_seconds: int = 300

    # Internal (service-to-service) SPIFFE identities allowed on /internal/v1.
    spiffe_header: str = "x-client-spiffe-id"
    internal_allowed_spiffe: list[str] = [
        "spiffe://windrose/ns/data/sa/pipeline-orchestrator",
        "spiffe://windrose/ns/data/sa/experiment-service",
        "spiffe://windrose/ns/data/sa/dataset-service",
        "spiffe://windrose/ns/tools/sa/mcp-gateway",
    ]

    # Adapter selection: True (the default) wires the real adapters against local
    # infra (MLflow, MinIO/S3, Redpanda, OPA, Redis, Postgres) — app.main boots
    # real by default. Unit/integration tests pass ``use_real_adapters=False`` to
    # wire in-memory doubles (never reachable from the default runtime).
    use_real_adapters: bool = True

    # Real infra endpoints (deploy/docker-compose.dev.yml defaults)
    mlflow_tracking_uri: str = "http://localhost:5500"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "windrose"
    s3_secret_key: str = "windrose_dev"
    s3_region: str = "us-east-1"
    datasets_bucket: str = "windrose-datasets"
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    opa_url: str = "http://localhost:8281"

    events_topic: str = "inference.events.v1"
    pipeline_events_topic: str = "pipeline.events.v1"
    experiment_events_topic: str = "experiment.events.v1"
    dataset_events_topic: str = "dataset.events.v1"
    usage_events_topic: str = "usage.events.v1"

    # Job policy (INF-FR-002/008/042, BR-12)
    default_allowed_stages: list[str] = ["production", "staging"]
    max_concurrent_inference_jobs: int = 5
    queue_depth_cap: int = 100
    queued_timeout_minutes: int = 60
    max_run_duration_hours: int = 8
    lineage_finalize_retry_minutes: int = 60
    finalize_max_attempts: int = 3

    # Scheduling (INF-FR-050..055)
    scheduler_enabled: bool = True
    scheduler_tick_seconds: float = 5.0
    schedule_circuit_breaker: int = 3
    max_enabled_schedules: int = 50

    # Background workers (real runtime)
    run_executor_enabled: bool = True
    consumers_enabled: bool = True
    outbox_relay_enabled: bool = True

    # Deploy-time action-catalog registration (RBC-FR-022).
    rbac_url: str | None = None
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None
