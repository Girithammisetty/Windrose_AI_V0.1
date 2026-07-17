from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PPL_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "pipeline-orchestrator"

    # Runtime DSN uses the NON-OWNER, non-superuser DML role ``pipeline_app`` so RLS
    # (which is FORCED in the schema) always applies. Migrations run as a privileged
    # role via PPL_MIGRATE_URL — never as this role.
    database_url: str = (
        "postgresql+asyncpg://pipeline_app:pipeline_app@localhost:5432/pipeline"
    )

    # AuthN (MASTER-FR-010/011). Dev/tests use a static PEM; prod uses JWKS.
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = None
    jwks_ttl_seconds: int = 300

    # Internal service-to-service auth (SPIFFE via the mesh; MASTER-FR-014).
    spiffe_header: str = "x-client-spiffe-id"
    internal_allowed_spiffe: list[str] = [
        "spiffe://windrose/ns/ml/sa/pipeline-orchestrator",
        "spiffe://windrose/ns/ml/sa/inference-service",
        "spiffe://windrose/ns/data/sa/dataset-service",
        "spiffe://windrose/ns/tools/sa/mcp-gateway",
        "spiffe://windrose/ns/ml/sa/base-component",
    ]

    # Adapter selection. True (the DEFAULT — the shipped image wires this) → the shared
    # windrose_common real adapters (SQL RLS UoW, RedisDedupStore, OpaAuthzClient,
    # S3 manifest store) + the real local training executor + real MLflow gateway.
    # False → the in-memory unit/dev doubles, reachable ONLY from tests (conftest sets
    # it False). The in-memory doubles are never reachable from the default runtime.
    use_real_adapters: bool = True

    # Local (unit tier) object store for compiled manifests + feature datasets.
    object_store_dir: str = "/tmp/windrose/pipeline-orchestrator/objects"

    # Real-adapter endpoints (deploy/docker-compose.dev.yml defaults).
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "windrose"
    s3_secret_key: str = "windrose_dev"
    s3_region: str = "us-east-1"
    artifacts_bucket: str = "windrose-pipelines"
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    opa_url: str = "http://localhost:8281"

    # Real MLflow tracking/registry (the training runs log here; :5500 per compose).
    mlflow_tracking_uri: str = "http://localhost:5500"
    mlflow_experiment: str = "windrose-pipeline-orchestrator"

    # dataset-service internal rows API (a read-from-warehouse node reads an uploaded
    # dataset's rows at run time). The SPIFFE below is in dataset-service's internal
    # allowlist; the tenant is sent per-request via x-windrose-tenant-id.
    dataset_service_url: str = "http://localhost:8304"
    dataset_reader_spiffe: str = "spiffe://windrose/ns/data/sa/pipeline-orchestrator"

    # Execution backend: "local" (real training on the Mac, the default) or
    # "argo" (infra-gated — needs a k8s cluster + Argo Workflows server).
    executor_backend: str = "local"
    argo_server_url: str = "http://localhost:2746"

    # Quotas / rate limits (BRD §7 defaults; per-tenant override in tenant_quotas).
    default_max_concurrent_runs: int = 10
    default_max_concurrent_pods: int = 40
    default_max_run_duration_minutes: int = 480
    default_min_seconds_between_runs: int = 15
    max_queue_depth: int = 50

    # Recurring pipeline scheduling (PIPE-FR-050). The background ticker polls
    # fire_due(now) every scheduler_poll_seconds. Gated so unit tests never start
    # it (the lifespan only starts workers under use_real_adapters anyway, but the
    # flag lets an operator disable the ticker independently).
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = 30.0

    events_topic: str = "pipeline.events.v1"
    case_topic: str = "case.events.v1"
    identity_topic: str = "identity.events.v1"

    component_catalog_version: str = "windrose-catalog/1.0.0"

    # Deploy-time action-catalog registration (RBC-FR-022).
    rbac_url: str | None = None
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None
