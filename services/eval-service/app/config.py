from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVAL_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "eval-service"

    # Runtime DSN uses the non-owner, non-superuser DML role ``eval_app_rt`` (member
    # of the ``eval_app`` group; provisioned by the migration). FORCE ROW LEVEL
    # SECURITY + a non-owner role means RLS is enforced even for the table owner
    # (superusers bypass RLS, so the runtime role must never be one). Migrations run
    # under a privileged role via EVAL_MIGRATE_URL.
    database_url: str = "postgresql+asyncpg://eval_app_rt:eval_app_dev@localhost:5432/eval"

    # AuthN (MASTER-FR-010/011). In dev/tests a static PEM is used; prod uses JWKS.
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = None
    jwks_ttl_seconds: int = 300

    # Internal (service-to-service) auth: SPIFFE identity forwarded by the mesh
    # sidecar after mTLS termination (MASTER-FR-014). CI + agent-registry call
    # the gate/CI plane over mTLS.
    spiffe_header: str = "x-client-spiffe-id"
    internal_allowed_spiffe: list[str] = [
        "spiffe://windrose/ns/ci/sa/pipeline",
        "spiffe://windrose/ns/ai/sa/agent-registry",
        "spiffe://windrose/ns/ai/sa/agent-runtime",
    ]
    # eval-service's own SPIFFE identity when it calls ai-gateway's internal
    # key-mint path (AIG-FR-032; ai-gateway allowlists this identity).
    eval_spiffe_id: str = "spiffe://windrose/ns/ai/sa/eval-service"

    # Adapter selection. Defaults **True** (CONVENTIONS.md END STATE: real adapters
    # are the default; the in-memory doubles must never be reachable from app.main
    # wiring). True wires the shared windrose_common adapters against local infra
    # (Redpanda, OPA, Redis) + SQL/RLS store. Only the test suite sets it False to
    # reach the unit/dev-tier doubles.
    use_real_adapters: bool = True

    # Real-adapter endpoints (deploy/docker-compose.dev.yml defaults)
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    opa_url: str = "http://localhost:8281"

    events_topic: str = "eval.events.v1"

    # LLM-judge via the real ai-gateway (BRD 12; judge request class). Judges call
    # ai-gateway's OpenAI-compatible data plane with a virtual key + platform JWT.
    ai_gateway_url: str = "http://localhost:8312"
    ai_gateway_chat_path: str = "/v1/chat/completions"
    ai_gateway_model: str = "windrose-auto"
    ai_gateway_virtual_key: str | None = None
    judge_request_class: str = "judge"
    judge_timeout_s: float = 120.0

    # agent-runtime replay/no-side-effect executor (EVL-FR-020). When set, eval
    # runs fetch candidate outputs live; otherwise CI supplies candidate outputs.
    agent_runtime_url: str | None = None
    # Signing key eval mints its own short-lived platform JWT for judge calls
    # (verified by ai-gateway's JWKS/PEM). Same key used for rbac registration.
    judge_jwt_signing_key_pem: str | None = None
    judge_jwt_signing_kid: str | None = None

    # Fixture warehouse for sql_result_equivalence (BR-4/BR-9): a read-only
    # embedded DuckDB eval schema seeded per dataset version.
    fixture_warehouse_dir: str = "/tmp/windrose/eval-service/fixtures"
    sql_execution_ceiling_s: float = 60.0
    fixture_row_cap: int = 100_000

    # Eval-run budget caps (BRD 12 BR-7 / EVL-FR-023). Per-run USD ceiling.
    default_run_cost_cap_usd: float = 5.0

    # Langfuse (EVL-FR-060). Enrichment only; absence never fails gates (BR-13).
    langfuse_url: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    # Deploy-time action-catalog registration (RBC-FR-022).
    rbac_url: str | None = None
    register_signing_key_pem: str | None = None
    register_signing_kid: str | None = None
    register_tenant_id: str | None = None

    # SLO defaults
    slo_windows: list[str] = ["1h", "24h", "7d", "30d"]
