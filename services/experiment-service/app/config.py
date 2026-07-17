from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXP_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "experiment-service"

    # Runtime DSN uses the NON-privileged, non-owner ``experiment_app`` login role
    # (created by migration 0002) so Postgres RLS actually applies to the running
    # service (MASTER-FR-001). Migrations run separately as a privileged role via
    # EXP_MIGRATE_URL. The service must NEVER connect as the superuser/table owner
    # (which would bypass RLS even with policies enabled).
    database_url: str = (
        "postgresql+asyncpg://experiment_app:experiment_app@localhost:5432/experiment"
    )

    # AuthN (MASTER-FR-010/011). In dev/tests a static PEM is used; prod uses JWKS.
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = None
    jwks_ttl_seconds: int = 300

    # Internal (service-to-service) auth: SPIFFE identity forwarded by the mesh
    # sidecar after mTLS termination (MASTER-FR-014). MLflow webhook deliveries
    # additionally carry an HMAC body signature (EXP-FR-010).
    spiffe_header: str = "x-client-spiffe-id"
    internal_allowed_spiffe: list[str] = [
        "spiffe://windrose/ns/ml/sa/mlflow",
        "spiffe://windrose/ns/ml/sa/pipeline-orchestrator",
        "spiffe://windrose/ns/ml/sa/inference-service",
        "spiffe://windrose/ns/platform/sa/operator",
    ]

    # Adapter selection. True (the DEFAULT) wires the shared windrose_common
    # adapters + real MLflow against local infra (Postgres RLS, Redpanda+outbox,
    # Redis dedup, OPA, MinIO) — app.main runs fully real out of the box. Unit
    # tests explicitly set this False to wire the in-memory doubles.
    use_real_adapters: bool = True

    # Real-adapter endpoints (deploy/docker-compose.dev.yml defaults)
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    opa_url: str = "http://localhost:8281"

    # Real MLflow tracking + registry (system of truth this service mirrors).
    mlflow_tracking_uri: str = "http://localhost:5500"
    mlflow_rate_limit_rps: int = 5  # BR-14 per-tenant sweep cap

    # Webhook ingest (EXP-FR-010): shared HMAC secret + replay window + body cap.
    webhook_hmac_secret: str = "windrose-mlflow-webhook-dev-secret"
    webhook_signature_header: str = "x-mlflow-signature"
    webhook_delivery_header: str = "x-mlflow-delivery-id"
    webhook_replay_window_seconds: int = 300
    webhook_max_body_bytes: int = 256 * 1024

    # Object storage for signed artifact URLs (EXP-FR-014). MinIO S3.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "windrose"
    s3_secret_key: str = "windrose_dev"
    s3_region: str = "us-east-1"
    signed_url_ttl_seconds: int = 900

    # Reconciliation sweep (EXP-FR-013): interval + page size + drift alert.
    reconcile_interval_seconds: int = 900  # 15 min
    reconcile_page_size: int = 1000
    reconcile_drift_alert_streak: int = 3

    # Promotion approval gate (EXP-FR-033/BR-7).
    promotion_expiry_days: int = 14
    promotion_expiry_interval_seconds: int = 3600
    model_approver_role: str = "model_approver"

    # Comparison / query limits (EXP-FR-020/050, BR-9/BR-10).
    compare_min_runs: int = 2
    compare_max_runs: int = 20
    compare_default_page_size: int = 50
    query_max_metric_predicates: int = 3
    query_max_param_predicates: int = 3
    loss_metric_prefixes: list[str] = ["loss", "rmse", "mae", "mse", "error"]

    events_topic: str = "experiment.events.v1"
    pipeline_topic: str = "pipeline.events.v1"
    dataset_topic: str = "dataset.events.v1"
    identity_topic: str = "identity.events.v1"

    # Deploy-time action-catalog registration (RBC-FR-022).
    rbac_url: str | None = None
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None
