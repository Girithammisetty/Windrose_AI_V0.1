"""Service settings. Values are plain-data so tests can construct/override freely."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

MIB = 1024 * 1024


@dataclass
class Settings:
    database_url: str = "sqlite+aiosqlite:///./.data/dev.db"
    environment: str = "dev"  # dev | test | prod
    data_dir: str = "./.data"

    # AuthN (MASTER-FR-010/011). PEM of the identity-service RS256 public key
    # (dev/tests); production verifies via cached JWKS refresh (real, py-common).
    jwt_public_key_pem: str | None = None
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwks_url: str | None = None
    jwks_ttl_seconds: int = 300

    # Adapter selection: "memory" wires the in-memory/local test doubles (unit
    # tier); "real" wires the shared windrose_common adapters against local infra
    # (MinIO, Iceberg REST, Vault, Redpanda, OPA, Redis) — the runtime default.
    adapter_mode: str = "memory"

    # Real-adapter endpoints (deploy/docker-compose.dev.yml defaults)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "windrose"
    s3_secret_key: str = "windrose_dev"
    s3_region: str = "us-east-1"
    uploads_bucket: str = "windrose-uploads"
    iceberg_catalog_uri: str = "http://localhost:8181"
    iceberg_warehouse: str = "s3://windrose-warehouse/"
    vault_addr: str = "http://localhost:8200"
    vault_token: str = "windrose_dev_root"

    # Secrets backend selection (BYO Infra Hardening Phase 2,
    # docs/design/byo-infra-hardening.md): vault|aws|azure|gcp. Default "vault"
    # preserves all existing behavior unchanged when unset.
    secrets_backend: str = "vault"
    aws_region: str = "us-east-1"
    aws_secrets_endpoint_url: str | None = None  # e.g. LocalStack
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    azure_key_vault_url: str | None = None
    gcp_project_id: str | None = None

    kafka_bootstrap_servers: str = "localhost:9092"
    opa_url: str = "http://localhost:8281"
    redis_url: str = "redis://localhost:6379/0"

    # Execution behaviour
    inline_execution: bool = True  # run jobs inline (dev/test); prod uses Temporal (stub)
    retry_max_attempts: int = 5  # ING-FR-081
    retry_backoff_base_s: float = 10.0
    retry_backoff_cap_s: float = 600.0
    progress_min_interval_s: float = 5.0  # ING-FR-026

    # Tenant caps (ING-FR-082)
    max_running_per_tenant: int = 5
    max_active_uploads_per_tenant: int = 20

    # Upload protocol (ING-FR-040)
    default_part_size: int = 32 * MIB
    min_part_size: int = 8 * MIB
    max_part_size: int = 64 * MIB
    upload_ttl_hours: int = 24

    # Webhook mode (ING-FR-024)
    webhook_max_payload_bytes: int = 1 * MIB

    # Decode / query streaming
    decode_batch_size: int = 5000
    query_batch_size: int = 10_000  # ING-FR-023
    query_timeout_s: int = 1600  # V1 TIMEOUT_INTERVAL parity, max 3600

    # Connection probes (ING-FR-004/005)
    connection_test_timeout_s: float = 15.0
    preview_timeout_s: float = 30.0

    # Secret lifecycle (ING-FR-006)
    vault_destroy_grace_days: int = 7

    # Internal service-to-service auth (SPIFFE via the mesh; MASTER-FR-014).
    # Gates /internal/v1/mcp/invoke, the MCP backend facade tool-plane's
    # mcp-gateway federates approved write-proposal tool execution to
    # (TPL-FR-012). Mirrors pipeline-orchestrator's config exactly.
    spiffe_header: str = "x-client-spiffe-id"
    internal_allowed_spiffe: list[str] = field(
        default_factory=lambda: ["spiffe://windrose/ns/tools/sa/mcp-gateway"]
    )

    # Deploy-time action-catalog registration (RBC-FR-022).
    rbac_url: str | None = None
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            rbac_url=os.getenv("RBAC_URL"),
            register_signing_key_pem=os.getenv("REGISTER_SIGNING_KEY_PEM"),
            register_signing_kid=os.getenv("REGISTER_SIGNING_KID"),
            register_tenant_id=os.getenv("REGISTER_TENANT_ID"),
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            environment=os.getenv("WINDROSE_ENV", cls.environment),
            data_dir=os.getenv("WINDROSE_DATA_DIR", cls.data_dir),
            jwt_public_key_pem=os.getenv("JWT_PUBLIC_KEY_PEM"),
            jwt_issuer=os.getenv("JWT_ISSUER", cls.jwt_issuer),
            jwt_audience=os.getenv("JWT_AUDIENCE", cls.jwt_audience),
            jwks_url=os.getenv("JWKS_URL"),
            # runtime defaults to the real adapters; override with ADAPTER_MODE=memory
            adapter_mode=os.getenv("ADAPTER_MODE", "real"),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL", cls.s3_endpoint_url),
            iceberg_catalog_uri=os.getenv("ICEBERG_CATALOG_URI", cls.iceberg_catalog_uri),
            vault_addr=os.getenv("VAULT_ADDR", cls.vault_addr),
            vault_token=os.getenv("VAULT_TOKEN", cls.vault_token),
            secrets_backend=os.getenv("SECRETS_BACKEND", cls.secrets_backend),
            aws_region=os.getenv("AWS_REGION", cls.aws_region),
            aws_secrets_endpoint_url=os.getenv("AWS_SECRETS_ENDPOINT_URL"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            azure_key_vault_url=os.getenv("AZURE_KEY_VAULT_URL"),
            gcp_project_id=os.getenv("GCP_PROJECT_ID"),
            kafka_bootstrap_servers=os.getenv(
                "KAFKA_BOOTSTRAP_SERVERS", cls.kafka_bootstrap_servers
            ),
            opa_url=os.getenv("OPA_URL", cls.opa_url),
            redis_url=os.getenv("REDIS_URL", cls.redis_url),
        )
