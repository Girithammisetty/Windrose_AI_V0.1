# Boot functions for every money-path service. Sourced by run.sh (which defines
# say/ok/warn/die, boot, build_go, wait_ready, track_pid, and all config env).
# Every service is wired to REAL infra (Postgres/Redis/Redpanda/OPA/MinIO/Iceberg/
# OpenSearch/Ollama/Temporal) and to each other; JWTs verify against the harness JWKS.

# ---- identity-service (Go). Boots twice: 1st to create schema, then seed signing
#      key + cells, then restart so the KeyManager cache loads the harness key. ----
start_identity() {
  # Guard against the stale-process class of bug this has hit repeatedly:
  # `boot identity ...` below tracks only the PID IT starts, so if a stale
  # identity-service from an earlier, untracked boot (a different terminal, a
  # previous session, a crashed script) is still bound to PORT_IDENTITY, the
  # freshly-built binary fails to bind and exits immediately — but the OLD
  # process keeps answering /readyz 200, so wait_ready reports success while
  # every subsequent request is served by code that predates this boot
  # entirely. Kill whatever ACTUALLY holds the port first, unconditionally.
  local stale; stale="$(lsof -ti "tcp:$PORT_IDENTITY" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$stale" ]; then
    warn "port $PORT_IDENTITY already bound (pid $stale) — killing before boot"
    kill -9 $stale 2>/dev/null; sleep 1
  fi
  build_go identity-service identity-e2e cmd/server
  local env=( env
    MIGRATE_DATABASE_URL="${PG_BASE}/identity?sslmode=disable"
    DATABASE_URL="postgres://identity_app:identity_app@localhost:5432/identity?sslmode=disable"
    LISTEN_ADDR=":${PORT_IDENTITY}"
    KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL"
    REDIS_ADDR="$REDIS_ADDR"
    KEYCLOAK_URL="http://localhost:8180"
    KEYCLOAK_ADMIN_USER="admin" KEYCLOAK_ADMIN_PASSWORD="admin"
    OPA_URL="$OPA_URL" RBAC_URL="$RBAC_URL" )
  say "boot identity (bootstrap pass)"
  boot identity "${env[@]}" "$BIN_DIR/identity-e2e"
  wait_ready identity "$IDENTITY_URL" || die "identity failed first boot"
  seed_identity_prereqs         # signing key + cells (idempotent)
  say "restart identity to load harness signing key"
  kill "$(cat "$PID_DIR/identity.pid")" 2>/dev/null; sleep 1
  boot identity "${env[@]}" "$BIN_DIR/identity-e2e"
  wait_ready identity "$IDENTITY_URL" || die "identity failed second boot"
}

start_rbac() {
  build_go rbac-service rbac-e2e cmd/server
  say "boot rbac"
  boot rbac env \
    DATABASE_URL="postgres://rbac_app:rbac_app@localhost:5432/rbac?sslmode=disable" \
    MIGRATE_DATABASE_URL="${PG_BASE}/rbac?sslmode=disable" \
    LISTEN_ADDR=":${PORT_RBAC}" \
    REDIS_ADDR="$REDIS_ADDR" KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    CONSUME_TOPICS="identity.events.v1" \
    AUTH_JWKS_URL="${IDENTITY_URL}/.well-known/jwks.json" AUTH_ISSUER="$WR_ISS" AUTH_AUDIENCE="$WR_AUD" \
    "$BIN_DIR/rbac-e2e"
  wait_ready rbac "$RBAC_URL" || die "rbac not ready"
}

start_case() {
  build_go case-service case-e2e cmd/server
  say "boot case-service"
  local reg_key; reg_key="$(cat "$E2E_DIR/keys/idp_private.pem")"
  boot case env \
    MIGRATE_DATABASE_URL="${PG_BASE}/case_svc?sslmode=disable" \
    DATABASE_URL="postgres://case_app:case_app@localhost:5432/case_svc?sslmode=disable" \
    LISTEN_ADDR=":${PORT_CASE}" \
    OPENSEARCH_URL="$OPENSEARCH_URL" \
    KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    OPA_URL="$OPA_URL" REDIS_ADDR="$REDIS_ADDR" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    SNAPSHOT_ROOT="${E2E_DIR}/run/case-snapshots" \
    QUERY_SERVICE_URL="$QUERY_URL" \
    RBAC_URL="$RBAC_URL" REGISTER_SIGNING_KEY_PEM="$reg_key" \
    REGISTER_SIGNING_KID="e2e-harness-key-1" REGISTER_TENANT_ID="$TENANT_ID" \
    CASE_FACADE_ALLOWED_SPIFFE="spiffe://windrose/ns/tools/sa/mcp-gateway" \
    "$BIN_DIR/case-e2e"
  wait_ready case "$CASE_URL" || die "case-service not ready"
}

start_realtime() {
  build_go realtime-hub realtime-e2e cmd/server
  say "boot realtime-hub"
  local reg_key; reg_key="$(cat "$E2E_DIR/keys/idp_private.pem")"
  boot realtime env \
    MIGRATE_DATABASE_URL="${PG_BASE}/realtimehub?sslmode=disable" \
    DATABASE_URL="postgres://realtime_app:realtime_app@localhost:5432/realtimehub?sslmode=disable" \
    LISTEN_ADDR=":${PORT_REALTIME}" INTERNAL_LISTEN_ADDR=":${PORT_REALTIME_INTERNAL}" \
    REDIS_ADDR="$REDIS_ADDR" KAFKA_BROKERS="$KAFKA_BROKERS" \
    OPA_URL="$OPA_URL" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    RBAC_URL="$RBAC_URL" REGISTER_SIGNING_KEY_PEM="$reg_key" \
    REGISTER_SIGNING_KID="e2e-harness-key-1" REGISTER_TENANT_ID="$TENANT_ID" \
    "$BIN_DIR/realtime-e2e"
  wait_ready realtime "$REALTIME_URL" || die "realtime-hub not ready"
}

start_tool_plane() {
  build_go tool-plane tool-registry-e2e cmd/registry
  build_go tool-plane mcp-gateway-e2e cmd/gateway
  local reg_key; reg_key="$(cat "$E2E_DIR/keys/idp_private.pem")"
  say "boot tool-registry"
  boot toolreg env \
    MIGRATE_DATABASE_URL="${PG_BASE}/tool_plane?sslmode=disable" \
    DATABASE_URL="postgres://toolplane_app:toolplane_app@localhost:5432/tool_plane?sslmode=disable" \
    LISTEN_ADDR=":${PORT_TOOLREG}" \
    REDIS_ADDR="$REDIS_ADDR" KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    OLLAMA_URL="$OLLAMA_V1" EMBED_MODEL="nomic-embed-text" \
    OPA_URL="$OPA_URL" \
    RBAC_URL="$RBAC_URL" REGISTER_SIGNING_KEY_PEM="$reg_key" \
    REGISTER_SIGNING_KID="e2e-harness-key-1" REGISTER_TENANT_ID="$TENANT_ID" \
    "$BIN_DIR/tool-registry-e2e"
  wait_ready toolreg "$TOOL_REGISTRY_URL" || die "tool-registry not ready"
  say "boot mcp-gateway"
  boot gateway env \
    MIGRATE_DATABASE_URL="${PG_BASE}/tool_plane?sslmode=disable" \
    DATABASE_URL="postgres://toolplane_app:toolplane_app@localhost:5432/tool_plane?sslmode=disable" \
    LISTEN_ADDR=":${PORT_GATEWAY}" \
    REDIS_ADDR="$REDIS_ADDR" KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    OPA_URL="$OPA_URL" \
    PROPOSAL_JWKS_URL="${AGENT_RUNTIME_URL}/api/v1/.well-known/jwks.json" \
    PROPOSAL_ISSUER="windrose-agent-runtime" \
    RBAC_URL="$RBAC_URL" REGISTER_SIGNING_KEY_PEM="$reg_key" \
    REGISTER_SIGNING_KID="e2e-harness-key-1" REGISTER_TENANT_ID="$TENANT_ID" \
    "$BIN_DIR/mcp-gateway-e2e"
  wait_ready gateway "$MCP_GATEWAY_URL" || die "mcp-gateway not ready"
}

# ---- Python services ----
py_migrate() { # svc dir  (env vars already exported by caller)
  say "migrate $1"
  ( cd "$REPO_DIR/services/$1" && uv run alembic upgrade head ) >> "$LOG_DIR/$1.migrate.log" 2>&1 \
    || warn "$1 alembic returned nonzero (see $1.migrate.log)"
}
py_boot() { # name svc app_port  extra-env pairs passed via preset environment
  local name="$1" svc="$2" port="$3"
  boot "$name" bash -c "cd '$REPO_DIR/services/$svc' && exec uv run uvicorn app.main:app --host 0.0.0.0 --port $port"
}

start_ingestion() {
  # Runtime logs in as the non-superuser ingestion_app role so FORCE RLS is
  # enforced; migrations run privileged via INGESTION_MIGRATE_URL (windrose).
  export DATABASE_URL="postgresql+asyncpg://ingestion_app:ingestion_app@localhost:5432/ingestion"
  export INGESTION_MIGRATE_URL="${PG_SYNC_BASE}/ingestion"
  export ADAPTER_MODE=real WINDROSE_ENV=dev
  export S3_ENDPOINT_URL="$S3_ENDPOINT" ICEBERG_CATALOG_URI="$ICEBERG_URI"
  export VAULT_ADDR="$VAULT_ADDR_" VAULT_TOKEN="$VAULT_TOKEN_"
  export KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" OPA_URL="$OPA_URL" REDIS_URL="$REDIS_URL"
  export JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD"
  export RBAC_URL="$RBAC_URL" REGISTER_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export REGISTER_SIGNING_KID="e2e-harness-key-1" REGISTER_TENANT_ID="$TENANT_ID"
  py_migrate ingestion-service
  say "boot ingestion-service"; py_boot ingestion ingestion-service "$PORT_INGESTION"
  wait_ready ingestion "$INGESTION_URL" || die "ingestion not ready"
}

start_dataset() {
  # Runtime logs in as the non-superuser dataset_app role so FORCE RLS is
  # enforced; migrations run privileged via DST_MIGRATE_URL (task_971cc66f).
  export DST_DATABASE_URL="postgresql+asyncpg://dataset_app:dataset_app@localhost:5432/dataset"
  export DST_MIGRATE_URL="${PG_SYNC_BASE}/dataset"
  export DST_USE_REAL_ADAPTERS=true
  export DST_S3_ENDPOINT_URL="$S3_ENDPOINT" DST_S3_ACCESS_KEY=windrose DST_S3_SECRET_KEY=windrose_dev
  export DST_S3_REGION=us-east-1 DST_PROFILES_BUCKET=windrose-profiles
  export DST_ICEBERG_CATALOG_URI="$ICEBERG_URI" DST_ICEBERG_WAREHOUSE="s3://windrose-warehouse/"
  export DST_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" DST_REDIS_URL="$REDIS_URL" DST_OPA_URL="$OPA_URL"
  export DST_JWKS_URL="$WR_JWKS_URL" DST_JWT_ISSUER="$WR_ISS" DST_JWT_AUDIENCE="$WR_AUD"
  export DST_RBAC_URL="$RBAC_URL" DST_REGISTER_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export DST_REGISTER_SIGNING_KID="e2e-harness-key-1" DST_REGISTER_TENANT_ID="$TENANT_ID"
  py_migrate dataset-service
  say "boot dataset-service"; py_boot dataset dataset-service "$PORT_DATASET"
  wait_ready dataset "$DATASET_URL" || die "dataset not ready"
}

start_ai_gateway() {
  export AIG_USE_REAL_ADAPTERS=true
  # Runtime logs in as the non-superuser ai_gateway_app role so FORCE RLS is
  # enforced; migrations run privileged via AIG_MIGRATE_URL (windrose).
  export AIG_DATABASE_URL="postgresql+asyncpg://ai_gateway_app:ai_gateway_app@localhost:5432/ai_gateway"
  export AIG_MIGRATE_URL="${PG_SYNC_BASE}/ai_gateway"
  export AIG_REDIS_URL="$REDIS_URL" AIG_OLLAMA_BASE_URL="$OLLAMA_V1"
  export AIG_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" AIG_OPA_URL="$OPA_URL"
  export AIG_JWT_ISSUER="$WR_ISS" AIG_JWT_AUDIENCE="$WR_AUD" AIG_JWKS_URL="$WR_JWKS_URL"
  # Deploy-time action-catalog registration (RBC-FR-022) — without this,
  # rbac's projector never learns ai.* actions exist and OPA denies every
  # ai-gateway admin route regardless of role grants (action_known=false).
  export AIG_RBAC_URL="$RBAC_URL" AIG_REGISTER_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export AIG_REGISTER_SIGNING_KID="e2e-harness-key-1" AIG_REGISTER_TENANT_ID="$TENANT_ID"
  py_migrate ai-gateway
  say "boot ai-gateway"; py_boot aigw ai-gateway "$PORT_AIGW"
  wait_ready aigw "$AI_GATEWAY_URL" || die "ai-gateway not ready"
}

start_memory() {
  # Runtime logs in as the non-superuser memory_app role so FORCE RLS is
  # enforced; the admin pool (CREATE SCHEMA provisioning) and migrations stay
  # privileged as windrose via MEM_ADMIN_DATABASE_URL / MEM_MIGRATE_URL.
  export MEM_DATABASE_URL="postgresql+asyncpg://memory_app:memory_app@localhost:5432/memory"
  export MEM_ADMIN_DATABASE_URL="${PG_ASYNC_BASE}/memory"
  export MEM_MIGRATE_URL="${PG_SYNC_BASE}/memory"
  export MEM_EMBEDDINGS_BASE_URL="$OLLAMA_V1" MEM_EMBEDDINGS_MODEL="nomic-embed-text"
  export MEM_EMBEDDING_DIM=768 MEM_ACTIVE_EMBEDDING_VER="nomic-embed-text/v1"
  export MEM_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" MEM_REDIS_URL="$REDIS_URL" MEM_OPA_URL="$OPA_URL"
  export MEM_EVENTS_TOPIC="memory.events.v1"
  export MEM_JWKS_URL="$WR_JWKS_URL" MEM_JWT_ISSUER="$WR_ISS" MEM_JWT_AUDIENCE="$WR_AUD"
  export MEM_RBAC_URL="$RBAC_URL" MEM_REGISTER_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export MEM_REGISTER_SIGNING_KID="e2e-harness-key-1" MEM_REGISTER_TENANT_ID="$TENANT_ID"
  py_migrate memory-service
  say "boot memory-service"; py_boot memory memory-service "$PORT_MEMORY"
  wait_ready memory "$MEMORY_URL" || die "memory not ready"
}

start_agent_runtime() { # arg: virtual key
  local vkey="$1"
  # Runtime logs in as the non-superuser agent_runtime_app role so FORCE RLS is
  # enforced; the admin pool and migrations stay privileged as windrose via
  # AR_ADMIN_DATABASE_URL / AR_MIGRATE_URL.
  export AR_DATABASE_URL="postgresql+asyncpg://agent_runtime_app:agent_runtime_app@localhost:5432/agent_runtime"
  export AR_ADMIN_DATABASE_URL="${PG_ASYNC_BASE}/agent_runtime"
  export AR_MIGRATE_URL="${PG_SYNC_BASE}/agent_runtime"
  export AR_USE_REAL_ADAPTERS=true
  export AR_TEMPORAL_TARGET="localhost:7233" AR_TEMPORAL_NAMESPACE="default" AR_TEMPORAL_TASK_QUEUE="agents-pool"
  export AR_USE_TEMPORAL=true
  export AR_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" AR_REDIS_URL="$REDIS_URL"
  export AR_OPA_URL="$OPA_URL" AR_OPA_PACKAGE="windrose/authz_input"
  export AR_AI_GATEWAY_URL="$AI_GATEWAY_URL" AR_AI_GATEWAY_CHAT_PATH="/v1/chat/completions"
  export AR_AI_GATEWAY_MODEL="windrose-auto" AR_AI_GATEWAY_VIRTUAL_KEY="$vkey" AR_AI_GATEWAY_REQUEST_CLASS="chat"
  export AR_TOOL_PLANE_URL="$MCP_GATEWAY_URL" AR_TOOL_PLANE_MCP_PATH="/mcp"
  export AR_MEMORY_SERVICE_URL="$MEMORY_URL" AR_CASE_SERVICE_URL="$CASE_URL" AR_REALTIME_HUB_URL="$REALTIME_URL"
  export AR_JWKS_URL="$WR_JWKS_URL" AR_JWT_ISSUER="$WR_ISS" AR_JWT_AUDIENCE="$WR_AUD"
  # agent-runtime signs BOTH its downstream OBO tokens AND proposal grants with this
  # key; give it the harness key + kid + issuer so its minted OBO tokens verify against
  # the harness JWKS at case/memory/ai-gateway, and tool-plane (which trusts agent-
  # runtime's JWKS) verifies its grants under the same kid.
  export AR_GRANT_PRIVATE_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export AR_GRANT_KID="e2e-harness-key-1"
  export AR_OBO_ISSUER="$WR_ISS"
  export AR_OBO_AUDIENCE="$WR_AUD"
  py_migrate agent-runtime
  say "boot agent-runtime (Temporal worker in-process)"; py_boot agent agent-runtime "$PORT_AGENT"
  wait_ready agent "$AGENT_RUNTIME_URL" || die "agent-runtime not ready"
}

# ---- RETRAIN TAIL services (pipeline-orchestrator, experiment-service,
#      inference-service). All default to REAL adapters + FORCE RLS + a non-owner
#      app role; migrations run as the privileged windrose role via *_MIGRATE_URL,
#      the service then logs in as its own app role. They train/register/score
#      against REAL MLflow (:5500) + MinIO + Kafka. ----

# The MLflow experiment pipeline-orchestrator logs training runs into is named to
# EXACTLY match the experiment experiment-service creates for {tenant}/{workspace}/
# claims-retrain, so experiment-service's reconciliation sweep (real MLflow REST)
# mirrors the run and it becomes registrable. Both derive WORKSPACE identically.
retrain_experiment_name() {
  "$PY" - "$TENANT_ID" <<'PY'
import sys, uuid
tenant = sys.argv[1]
ws = str(uuid.uuid5(uuid.NAMESPACE_DNS, "claims-triage-ws-" + tenant))
print(f"{tenant}/{ws}/claims-retrain")
PY
}

start_pipeline() {
  export PPL_DATABASE_URL="postgresql+asyncpg://pipeline_app:pipeline_app@localhost:5432/pipeline"
  export PPL_MIGRATE_URL="postgresql+psycopg://windrose:windrose_dev@localhost:5432/pipeline"
  export PPL_USE_REAL_ADAPTERS=true PPL_ENV=dev
  export PPL_MLFLOW_TRACKING_URI="$MLFLOW_URL"
  export PPL_MLFLOW_EXPERIMENT="$(retrain_experiment_name)"
  export PPL_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" PPL_REDIS_URL="$REDIS_URL" PPL_OPA_URL="$OPA_URL"
  export PPL_S3_ENDPOINT_URL="$S3_ENDPOINT" PPL_S3_ACCESS_KEY=windrose PPL_S3_SECRET_KEY=windrose_dev
  export PPL_S3_REGION=us-east-1 PPL_ARTIFACTS_BUCKET=windrose-pipelines
  export PPL_DEFAULT_MIN_SECONDS_BETWEEN_RUNS=0
  export PPL_JWKS_URL="$WR_JWKS_URL" PPL_JWT_ISSUER="$WR_ISS" PPL_JWT_AUDIENCE="$WR_AUD"
  echo "export PPL_MLFLOW_EXPERIMENT='$PPL_MLFLOW_EXPERIMENT'" >> "$PID_DIR/../context.env"
  py_migrate pipeline-orchestrator
  say "boot pipeline-orchestrator (real local training executor -> MLflow $MLFLOW_URL)"
  py_boot pipeline pipeline-orchestrator "$PORT_PIPELINE"
  wait_ready pipeline "$PIPELINE_URL" || die "pipeline-orchestrator not ready"
}

start_experiment() {
  export EXP_DATABASE_URL="postgresql+asyncpg://experiment_app:experiment_app@localhost:5432/experiment"
  export EXP_MIGRATE_URL="postgresql+psycopg://windrose:windrose_dev@localhost:5432/experiment"
  export EXP_USE_REAL_ADAPTERS=true EXP_ENV=dev
  export EXP_MLFLOW_TRACKING_URI="$MLFLOW_URL"
  export EXP_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" EXP_REDIS_URL="$REDIS_URL" EXP_OPA_URL="$OPA_URL"
  export EXP_S3_ENDPOINT_URL="$S3_ENDPOINT" EXP_S3_ACCESS_KEY=windrose EXP_S3_SECRET_KEY=windrose_dev
  export EXP_S3_REGION=us-east-1
  export EXP_JWKS_URL="$WR_JWKS_URL" EXP_JWT_ISSUER="$WR_ISS" EXP_JWT_AUDIENCE="$WR_AUD"
  # Was missing entirely (found live via task #64's ml-journeys.spec.ts:
  # experiment.run.update was declared in registration.py's MANIFEST but never
  # reached the shared perm:catalog:actions key because register_actions()'s
  # first guard, `if not settings.rbac_url or not settings.register_signing_key_pem`,
  # always short-circuited with these unset — every experiment-service action
  # was permanently unregistered, not just this one).
  export EXP_RBAC_URL="$RBAC_URL" EXP_REGISTER_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export EXP_REGISTER_SIGNING_KID="e2e-harness-key-1" EXP_REGISTER_TENANT_ID="$TENANT_ID"
  py_migrate experiment-service
  say "boot experiment-service (MLflow mirror + governed promotion gate)"
  py_boot experiment experiment-service "$PORT_EXPERIMENT"
  wait_ready experiment "$EXPERIMENT_URL" || die "experiment-service not ready"
}

start_inference() {
  export INF_DATABASE_URL="postgresql+asyncpg://inference_app:inference_app@localhost:5432/inference"
  export INF_MIGRATE_URL="postgresql+psycopg://windrose:windrose_dev@localhost:5432/inference"
  export INF_USE_REAL_ADAPTERS=true INF_ENV=dev
  export INF_MLFLOW_TRACKING_URI="$MLFLOW_URL"
  export INF_S3_ENDPOINT_URL="$S3_ENDPOINT" INF_S3_ACCESS_KEY=windrose INF_S3_SECRET_KEY=windrose_dev
  export INF_S3_REGION=us-east-1 INF_DATASETS_BUCKET=windrose-datasets
  export INF_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" INF_REDIS_URL="$REDIS_URL" INF_OPA_URL="$OPA_URL"
  export INF_JWKS_URL="$WR_JWKS_URL" INF_JWT_ISSUER="$WR_ISS" INF_JWT_AUDIENCE="$WR_AUD"
  # inference-service's model-registry adapter sets the tracking URI only on its own
  # MLflow client, but models:/ artifact+registry resolution reads MLflow's GLOBAL URI
  # (unset -> local ./mlruns file store, "Registered Model not found"). Point the
  # standard MLflow env vars at the real server so models:/ resolves. (Owning-service
  # bug: MlflowModelRegistry should set_tracking_uri/set_registry_uri globally.)
  export MLFLOW_TRACKING_URI="$MLFLOW_URL" MLFLOW_REGISTRY_URI="$MLFLOW_URL"
  export MLFLOW_S3_ENDPOINT_URL="$S3_ENDPOINT" AWS_ACCESS_KEY_ID=windrose AWS_SECRET_ACCESS_KEY=windrose_dev
  # Same class of gap as start_experiment above (task #64 finding) — was
  # missing entirely. inference.* actions currently happen to already be in
  # perm:catalog:actions from an earlier registration, but a truly clean boot
  # would silently skip registration the same way experiment-service did.
  export INF_RBAC_URL="$RBAC_URL" INF_REGISTER_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export INF_REGISTER_SIGNING_KID="e2e-harness-key-1" INF_REGISTER_TENANT_ID="$TENANT_ID"
  py_migrate inference-service
  say "boot inference-service (real batch scoring: MLflow model load + MinIO parquet)"
  py_boot inference inference-service "$PORT_INFERENCE"
  wait_ready inference "$INFERENCE_URL" || die "inference-service not ready"
}

# ======================================================================
# ==  `make up` ONLY: the remaining services that complete the platform  =
# ==  query, semantic, eval, chart, usage, audit, notification. Not      =
# ==  booted by `make e2e` (money-path). Each wired to real infra + peers.=
# ======================================================================

start_query() {
  # query-service embeds DuckDB (CGO). RESULTS_ROOT default (/var/lib) is not
  # writable on a Mac -> point it at a writable run dir.
  say "build query-e2e (CGO duckdb)"
  ( cd "$REPO_DIR/services/query-service" && CGO_ENABLED=1 go build -o "$BIN_DIR/query-e2e" ./cmd/server ) || die "build query failed"
  mkdir -p "$E2E_DIR/run/query-results"
  say "boot query-service"
  local reg_key; reg_key="$(cat "$E2E_DIR/keys/idp_private.pem")"
  boot query env \
    MIGRATE_DATABASE_URL="${PG_BASE}/query?sslmode=disable" \
    DATABASE_URL="postgres://query_app:query_app@localhost:5432/query?sslmode=disable" \
    LISTEN_ADDR=":${PORT_QUERY}" \
    RESULTS_ROOT="$E2E_DIR/run/query-results" \
    DATASET_SERVICE_URL="$DATASET_URL" \
    OPA_URL="$OPA_URL" REDIS_ADDR="$REDIS_ADDR" \
    KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    RBAC_URL="$RBAC_URL" REGISTER_SIGNING_KEY_PEM="$reg_key" \
    REGISTER_SIGNING_KID="e2e-harness-key-1" REGISTER_TENANT_ID="$TENANT_ID" \
    S3_ENDPOINT="localhost:9000" AWS_REGION="us-east-1" \
    AWS_ACCESS_KEY_ID="windrose" AWS_SECRET_ACCESS_KEY="windrose_dev" \
    DUCKDB_AUTOMATERIALIZE_SCHEMAS="main" \
    TRINO_ENDPOINT="http://localhost:8080" TRINO_USER="windrose" TRINO_CATALOG="iceberg" \
    "$BIN_DIR/query-e2e"
  wait_ready query "$QUERY_URL" || { warn "query-service not ready — SKIPPED"; SKIPPED+=("query"); return 1; }
}

start_semantic() {
  export SEM_USE_REAL_ADAPTERS=true
  export SEM_DATABASE_URL="postgresql+asyncpg://semantic:semantic@localhost:5432/semantic"
  export SEM_MIGRATE_URL="${PG_SYNC_BASE}/semantic"
  export SEM_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" SEM_REDIS_URL="$REDIS_URL" SEM_OPA_URL="$OPA_URL"
  export SEM_DATASET_SERVICE_URL="$DATASET_URL" SEM_QUERY_SERVICE_URL="$QUERY_URL"
  export SEM_EMBEDDINGS_BASE_URL="$OLLAMA_V1" SEM_EMBEDDINGS_MODEL="nomic-embed-text"
  export SEM_JWKS_URL="$WR_JWKS_URL" SEM_JWT_ISSUER="$WR_ISS" SEM_JWT_AUDIENCE="$WR_AUD"
  export SEM_RBAC_URL="$RBAC_URL" SEM_REGISTER_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export SEM_REGISTER_SIGNING_KID="e2e-harness-key-1" SEM_REGISTER_TENANT_ID="$TENANT_ID"
  py_migrate semantic-service
  # semantic migration creates the semantic_app NOLOGIN group but not a login role.
  psql_q -d semantic -tc "SELECT 1 FROM pg_roles WHERE rolname='semantic'" 2>/dev/null | grep -q 1 \
    || psql_q -d semantic -c "CREATE ROLE semantic LOGIN PASSWORD 'semantic' IN ROLE semantic_app" >/dev/null 2>&1
  say "boot semantic-service"; py_boot semantic semantic-service "$PORT_SEMANTIC"
  wait_ready semantic "$SEMANTIC_URL" || { warn "semantic-service not ready — SKIPPED"; SKIPPED+=("semantic"); return 1; }
}

start_eval() {
  export EVAL_USE_REAL_ADAPTERS=true
  export EVAL_DATABASE_URL="postgresql+asyncpg://eval_app_rt:eval_app_dev@localhost:5432/eval"
  export EVAL_MIGRATE_URL="${PG_SYNC_BASE}/eval"
  export EVAL_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" EVAL_REDIS_URL="$REDIS_URL" EVAL_OPA_URL="$OPA_URL"
  export EVAL_AI_GATEWAY_URL="$AI_GATEWAY_URL"
  export EVAL_JWKS_URL="$WR_JWKS_URL" EVAL_JWT_ISSUER="$WR_ISS" EVAL_JWT_AUDIENCE="$WR_AUD"
  # Deploy-time action-catalog registration (RBC-FR-022) — without this,
  # rbac's projector never learns eval.* actions exist and OPA denies every
  # eval-service route regardless of role grants (action_known=false).
  export EVAL_RBAC_URL="$RBAC_URL" EVAL_REGISTER_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export EVAL_REGISTER_SIGNING_KID="e2e-harness-key-1" EVAL_REGISTER_TENANT_ID="$TENANT_ID"
  # LLM-judge auth (EVL-FR-012): the AiGatewayJudgeClient needs BOTH a virtual key
  # AND a self-minted platform JWT, or ai-gateway 401s every judge scorer.
  #  - judge signing key: reuse the harness IdP key + kid so the JWT verifies
  #    against the harness JWKS at ai-gateway (same key every other service uses).
  #  - virtual key: mint a tenant-scoped, JUDGE-capable key (a chat/embed-only key
  #    is 403'd on request-class 'judge'); reuses the deployment aigw seeded.
  export EVAL_JUDGE_JWT_SIGNING_KEY_PEM="$(cat "$E2E_DIR/keys/idp_private.pem")"
  export EVAL_JUDGE_JWT_SIGNING_KID="e2e-harness-key-1"
  local eval_vkey
  eval_vkey="$( cd "$E2E_DIR" && "$PY" lib/seed.py evalkey "$TENANT_ID" 2>>"$LOG_DIR/seed.log" )"
  if [ -n "$eval_vkey" ]; then
    export EVAL_AI_GATEWAY_VIRTUAL_KEY="$eval_vkey"
    ok "eval-service judge virtual key minted"
  else
    warn "eval judge virtual key not minted — LLM-judge scorers will 401"
  fi
  # Live-replay candidate provider (EVL-FR-020). NOTE: agent-runtime does not yet
  # implement POST /api/v1/replay (ART-FR-015) — with this set, eval degrades
  # HONESTLY (marks the run CANDIDATE_UNAVAILABLE) instead of scoring empty
  # candidates as real. Deterministic (non-LLM) scoring still works from inline
  # candidate outputs CI posts.
  export EVAL_AGENT_RUNTIME_URL="$AGENT_RUNTIME_URL"
  py_migrate eval-service
  say "boot eval-service"; py_boot eval eval-service "$PORT_EVAL"
  wait_ready eval "$EVAL_URL" || { warn "eval-service not ready — SKIPPED"; SKIPPED+=("eval"); return 1; }
}

start_chart() {
  build_go chart-service chart-e2e cmd/server
  mkdir -p "$E2E_DIR/run/chart-exports"
  local reg_key; reg_key="$(cat "$E2E_DIR/keys/idp_private.pem")"
  say "boot chart-service"
  boot chart env \
    MIGRATE_DATABASE_URL="${PG_BASE}/chart?sslmode=disable" \
    DATABASE_URL="postgres://chart_app:chart_app@localhost:5432/chart?sslmode=disable" \
    LISTEN_ADDR=":${PORT_CHART}" PUBLIC_URL="$CHART_URL" \
    EXPORT_ROOT="$E2E_DIR/run/chart-exports" \
    REDIS_ADDR="$REDIS_ADDR" OPA_URL="$OPA_URL" \
    KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    SEMANTIC_SERVICE_URL="$SEMANTIC_URL" QUERY_SERVICE_URL="$QUERY_URL" \
    DATASET_SERVICE_URL="$DATASET_URL" EXPERIMENT_SERVICE_URL="$EXPERIMENT_URL" \
    RBAC_URL="$RBAC_URL" PLATFORM_SIGNING_KEY_PEM="$reg_key" PLATFORM_SIGNING_KID="e2e-harness-key-1" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    "$BIN_DIR/chart-e2e"
  wait_ready chart "$CHART_URL" || { warn "chart-service not ready — SKIPPED"; SKIPPED+=("chart"); return 1; }
}

start_usage() {
  build_go usage-service usage-e2e cmd/server
  local reg_key; reg_key="$(cat "$E2E_DIR/keys/idp_private.pem")"
  say "boot usage-service"
  boot usage env \
    MIGRATE_DATABASE_URL="${PG_BASE}/usage?sslmode=disable" \
    DATABASE_URL="postgres://usage_app:usage_app@localhost:5432/usage?sslmode=disable" \
    LISTEN_ADDR=":${PORT_USAGE}" \
    REDIS_ADDR="$REDIS_ADDR" OPA_URL="$OPA_URL" \
    KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    RBAC_URL="$RBAC_URL" SERVICE_SIGNING_KEY_PEM="$reg_key" SERVICE_SIGNING_KID="e2e-harness-key-1" \
    PLATFORM_TENANT_ID="$TENANT_ID" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    "$BIN_DIR/usage-e2e"
  wait_ready usage "$USAGE_URL" || { warn "usage-service not ready — SKIPPED"; SKIPPED+=("usage"); return 1; }
}

start_audit() {
  build_go audit-service audit-e2e cmd/server
  local reg_key; reg_key="$(cat "$E2E_DIR/keys/idp_private.pem")"
  say "boot audit-service (self-bootstraps PG db+role + ClickHouse)"
  # NOTE: explicit DATABASE_URL — the Python start_* funcs `export DATABASE_URL`
  # (async DSN) which would otherwise leak into audit's inherited env.
  boot audit env \
    ADMIN_DATABASE_URL="${PG_BASE}/postgres?sslmode=disable" \
    DATABASE_URL="postgres://audit_rw:audit_rw_dev@localhost:5432/audit?sslmode=disable" \
    AUDIT_DB_NAME="audit" \
    CLICKHOUSE_ADDR="localhost:9010" CLICKHOUSE_DB="audit" \
    CLICKHOUSE_USER="windrose" CLICKHOUSE_PASSWORD="windrose_dev" \
    LISTEN_ADDR=":${PORT_AUDIT}" \
    REDIS_ADDR="$REDIS_ADDR" OPA_URL="$OPA_URL" \
    KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    MINIO_ENDPOINT="localhost:9000" MINIO_ACCESS_KEY="windrose" MINIO_SECRET_KEY="windrose_dev" \
    RBAC_URL="$RBAC_URL" REGISTER_SIGNING_KEY_PEM="$reg_key" REGISTER_SIGNING_KID="e2e-harness-key-1" \
    REGISTER_TENANT_ID="$TENANT_ID" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    "$BIN_DIR/audit-e2e"
  wait_ready audit "$AUDIT_URL" || { warn "audit-service not ready — SKIPPED"; SKIPPED+=("audit"); return 1; }
}

start_notification() {
  build_go notification-service notification-e2e cmd/server
  local reg_key; reg_key="$(cat "$E2E_DIR/keys/idp_private.pem")"
  say "boot notification-service"
  # AWS_ACCESS_KEY_ID / SENDGRID_API_KEY / ACS_ENDPOINT are neutralized: the
  # inference start_* leaks AWS creds into the env, which would otherwise make
  # notification register a bogus SES provider. SMTP (local capture) is the only
  # real provider in dev.
  boot notification env \
    MIGRATE_DATABASE_URL="${PG_BASE}/notification?sslmode=disable" \
    DATABASE_URL="postgres://notif_app:notif_app_pw@localhost:5432/notification?sslmode=disable" \
    LISTEN_ADDR=":${PORT_NOTIFICATION}" \
    AWS_ACCESS_KEY_ID="" SENDGRID_API_KEY="" ACS_ENDPOINT="" \
    REDIS_ADDR="$REDIS_ADDR" OPA_URL="$OPA_URL" \
    KAFKA_BROKERS="$KAFKA_BROKERS" SCHEMA_REGISTRY_URL="$SCHEMA_REGISTRY_URL" \
    SMTP_ADDR="localhost:1025" WEBHOOK_ALLOW_HTTP="true" \
    RBAC_URL="$RBAC_URL" REGISTER_SIGNING_KEY_PEM="$reg_key" REGISTER_SIGNING_KID="e2e-harness-key-1" \
    REGISTER_TENANT_ID="$TENANT_ID" \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    "$BIN_DIR/notification-e2e"
  wait_ready notification "$NOTIFICATION_URL" || { warn "notification-service not ready — SKIPPED"; SKIPPED+=("notification"); return 1; }
}

# Boot the seven full-platform backend services (after boot_all()).
boot_platform_extra() {
  echo; say "PHASE 2b  booting the remaining platform services"
  start_query
  start_semantic
  start_chart
  start_usage
  start_audit
  start_notification
  start_eval
  echo; ok "full platform backend up (22 services incl. bff+ui booted separately)"
}

boot_all() {
  echo; say "PHASE 2  booting services"
  start_identity
  seed_tenant
  start_rbac
  start_realtime
  start_case
  start_ingestion
  start_dataset
  start_memory
  start_ai_gateway
  start_tool_plane
  # ai-gateway must be up before we seed its deployment + mint the agent's vkey.
  VKEY="$(seed_ai_gateway)"
  [ -n "$VKEY" ] || die "failed to seed ai-gateway model + virtual key"
  ok "ai-gateway seeded; agent virtual key minted"
  start_agent_runtime "$VKEY"
  # RETRAIN TAIL
  start_pipeline
  start_experiment
  start_inference
  echo; ok "all money-path services up (front half + retrain tail)"
}

run_driver() {
  echo; say "PHASE 3  driving the claims journey"
  "$PY" driver.py
  local rc=$?
  echo
  if [ $rc -eq 0 ]; then echo "${GRN}E2E PASSED${NC}"; else echo "${RED}E2E FAILED (rc=$rc)${NC}"; fi
  return $rc
}
