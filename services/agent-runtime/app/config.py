from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AR_", env_file=".env", extra="ignore")

    env: str = "dev"
    service_name: str = "agent-runtime"

    # App DB (non-privileged `agent_runtime_app` role in prod; RLS applies).
    database_url: str = (
        "postgresql+asyncpg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"
    )
    # Privileged connection for control-plane DDL only (migrations run separately).
    admin_database_url: str = (
        "postgresql+asyncpg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"
    )
    migrate_url: str | None = None  # psycopg sync URL for alembic (tests set this)

    # AuthN (MASTER-FR-010/011). Prod verifies incoming user/agent tokens via
    # identity-service JWKS; a static PEM takes precedence for dev/tests.
    jwt_issuer: str = "https://identity.windrose.local"
    jwt_audience: str = "windrose"
    jwt_public_key_pem: str | None = None
    jwks_url: str | None = "http://localhost:8300/jwks.json"
    jwks_ttl_seconds: int = 300

    # --- Proposal-execution grant SIGNING (we are the ISSUER). ---------------
    # RS256 private key (PEM) used to sign proposal grants + A2A cards, and the
    # matching kid published at our JWKS endpoint. tool-plane fetches our public
    # key via PROPOSAL_JWKS_URL and verifies iss == GRANT_ISSUER. When no PEM is
    # supplied the signer generates an ephemeral keypair at boot (dev/tests) and
    # serves it from /.well-known/agent-runtime-jwks.json — still a REAL RS256
    # signature over a REAL JWKS, never a stub.
    grant_private_key_pem: str | None = None
    grant_kid: str = "agent-runtime-2026-1"

    # Adapter selection (CONVENTIONS.md END STATE). RUNTIME DEFAULT True:
    # `app.main:app` / `make run` / Docker wire the REAL adapters against local
    # infra. The unit tier sets this False so in-memory doubles are reachable
    # ONLY from tests.
    use_real_adapters: bool = True
    store_mode: str | None = None  # "sql" (default when real) | "memory" (tests)

    # Real-adapter endpoints (deploy/docker-compose.dev.yml defaults).
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    opa_url: str = "http://localhost:8281"
    opa_package: str = "windrose/authz_input"

    # Temporal (ART-FR-010). Durable workflow store.
    temporal_target: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "agents-pool"
    # When True (runtime default) run execution is driven through a real Temporal
    # workflow. Tests that only exercise the graph directly may set False.
    use_temporal: bool = True

    # SLM distillation milestone 1: capture completed runs (+ human decisions)
    # into the governed agent_transcripts corpus. This is the tenant/deploy
    # CONSENT gate — off means no capture; the stored `consent` flag records it.
    slm_transcript_capture: bool = True
    # SLM distillation trainer backend (milestone 3). None/"" -> the honest
    # UnconfiguredGpuTrainer (jobs fail with gpu_trainer_not_configured); "fake"
    # -> deterministic in-process trainer (tests/demo); a real GPU backend
    # (e.g. "modal"/"sagemaker"/"k8s-job") is a GPU-gated follow-up.
    slm_trainer_backend: str | None = None

    # ai-gateway (ART-FR-012): ALL LLM calls go through the gateway (budget/
    # guardrails/metering), never direct to a provider. Dual credential per its
    # contract: Authorization: Bearer <virtual key> + X-Windrose-JWT: <jwt>.
    # `model` is a ladder alias ("windrose-auto"), NOT the concrete Ollama id.
    ai_gateway_url: str = "http://localhost:8312"
    ai_gateway_chat_path: str = "/v1/chat/completions"
    ai_gateway_model: str = "windrose-auto"
    ai_gateway_virtual_key: str | None = None  # nk-... minted per-run in prod
    ai_gateway_request_class: str = "chat"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 512

    # tool-plane mcp-gateway (ART-FR-012): ALL tool calls go through /mcp. The
    # signed grant travels in the JSON-RPC params._meta.proposal_grant field.
    tool_plane_url: str = "http://localhost:8311"
    tool_plane_mcp_path: str = "/mcp"

    # memory-service (RAG over resolved cases for the triage copilot).
    memory_service_url: str = "http://localhost:8307"

    # case-service (read a claim case to triage; plain REST at :8308).
    case_service_url: str = "http://localhost:8308"

    # rbac-service (resolve the CALLER's roles/capabilities so the copilot can
    # ground its persona/tone in the invoking user's role — GET /me/capabilities
    # with the caller's own token; :8302).
    rbac_service_url: str = "http://localhost:8302"

    # semantic-service (governed measures/dimensions grounding for the
    # dashboard-designer; MCP read-tool REST facade at :8086).
    semantic_service_url: str = "http://localhost:8086"

    # chart-service (chart-type catalog grounding + the chart.dashboard.create
    # write path the dashboard-designer proposes against; :8320).
    chart_service_url: str = "http://localhost:8320"

    # ingestion-service (grounding for the onboarding agent: connector-type
    # catalog + connection schema preview; plain REST at :8303).
    ingestion_service_url: str = "http://localhost:8303"

    # experiment-service (registered models + versions; grounding for the
    # inference agent — resolves the production model version + its input schema.
    # Also the model-training agent's history grounding — prior runs for an
    # algorithm via experiment.runs.search).
    experiment_service_url: str = "http://localhost:8314"

    # pipeline-orchestrator (algorithm-template catalog + parameter schema;
    # grounding for the model-training agent — the algorithm the run trains and
    # the template parameters the plan fills; plain REST at :8313).
    pipeline_orchestrator_url: str = "http://localhost:8313"

    # dataset-service (input-dataset schema/profile; grounding for the inference
    # agent's dataset<->model feature-compatibility check).
    dataset_service_url: str = "http://localhost:8304"

    # realtime-hub (ART-FR-070). Public listener (subscribers) + the SEPARATE
    # internal producer listener the runtime publishes stream events to
    # (POST {internal}/internal/v1/publish; deploy/e2e/config.env :8305/:8315).
    realtime_hub_url: str = "http://localhost:8305"
    realtime_hub_internal_url: str = "http://localhost:8315"

    # OBO minting: agent-runtime mints/requests agent_obo tokens for tool calls.
    # In dev/tests we self-sign with the grant key; prod exchanges via identity.
    obo_issuer: str = "https://identity.windrose.local"
    obo_audience: str = "windrose"

    # Session model (ART-FR-021).
    idle_timeout_seconds: int = 15 * 60
    max_lifetime_seconds: int = 8 * 3600

    # Proposal defaults (ART-FR-041).
    proposal_default_ttl_seconds: int = 7 * 24 * 3600
