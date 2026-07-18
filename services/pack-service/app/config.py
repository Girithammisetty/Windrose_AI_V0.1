from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """pack-service configuration (env prefix PACK_). Mirrors the platform's
    Python-service settings convention (see dataset-service/app/config.py)."""

    model_config = SettingsConfigDict(env_prefix="PACK_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "pack-service"

    database_url: str = "postgresql+asyncpg://pack:pack@localhost:5432/pack"

    # AuthN (MASTER-FR-010/011): static PEM in dev/tests, JWKS in prod.
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = None
    jwks_ttl_seconds: int = 300

    # rbac action-catalog registration (RBC-FR-022).
    rbac_url: str | None = "http://localhost:8302"
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None

    # The on-disk pack catalog. In the local stack this is the repo `packs/`
    # directory; a real deployment would resolve packs from the OCI registry
    # (PKG-FR-005) — deferred.
    packs_dir: str = "packs"

    # Downstream Core services the installer materializes into (defaults match
    # the local stack ports). The installer calls these AS THE INSTALLING USER
    # (the user's JWT is forwarded), so every write is authorized truthfully.
    ingestion_url: str = "http://localhost:8303"
    dataset_url: str = "http://localhost:8304"
    semantic_url: str = "http://localhost:8086"
    query_url: str = "http://localhost:8085"
    chart_url: str = "http://localhost:8320"
    case_url: str = "http://localhost:8308"
    rbac_svc_url: str = "http://localhost:8302"
    agent_url: str = "http://localhost:8306"
    memory_url: str = "http://localhost:8307"
    pipeline_url: str = "http://localhost:8313"
    identity_url: str = "http://localhost:8301"
    eval_url: str = "http://localhost:8324"

    use_real_adapters: bool = True

    # Authorization (MASTER-FR-012): OPA over the rbac projection in Redis.
    opa_url: str = "http://localhost:8281"
    redis_url: str = "redis://localhost:6379/0"
