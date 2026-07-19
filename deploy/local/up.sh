#!/usr/bin/env bash
# ============================================================================
# Windrose `make up` — provision the WHOLE platform locally and open it in a
# browser for hands-on end-user testing.
#
#   Preflight (docker+ollama+ports) -> infra up & healthy -> per-service DBs ->
#   migrate + boot all 22 services (real infra, wired to each other) -> platform
#   seed (tenant, 4 RBAC-gated personas) -> claims-vertical demo seed (claims
#   dataset, a queue of triage cases, a pending proposal, a trained+promoted
#   model — skip with --platform-only) -> print a banner with the URL and the
#   four logins.
#
# Honest by construction: every service is health-checked before we proceed;
# anything that cannot boot is reported, never faked. On a RAM-constrained Mac
# pass --core to boot the documented claims-showcase profile.
#
# Usage: deploy/local/up.sh [--core] [--skip-build] [--skip-seed] [--no-retrain] [--platform-only]
#
#   --platform-only  seed the tenant + four RBAC-gated personas only (Rule 3);
#                     skip the claims-vertical demo data (Rule 4 — vertical
#                     seeding is otherwise an Admin's job via the product UI).
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"     # ports, URLs, infra endpoints, dirs (E2E_DIR, REPO_DIR, PY, ...)

CORE=0; SKIP_BUILD=0; SKIP_SEED=0; PLATFORM_ONLY=0
for a in "$@"; do case "$a" in
  --core) CORE=1;;
  --skip-build) SKIP_BUILD=1;;
  --skip-seed) SKIP_SEED=1;;
  --no-retrain) export WINDROSE_SEED_RETRAIN=0;;
  --platform-only) PLATFORM_ONLY=1;;
esac; done

export PATH="/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:$PATH"
LOCAL_DIR="$(pwd)"
RUN_DIR="$LOCAL_DIR/run"
mkdir -p "$LOG_DIR" "$BIN_DIR" "$PID_DIR" "$RUN_DIR"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; BLD=$'\e[1m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }
die()  { echo "${RED}FATAL:${NC} $*" >&2; exit 1; }
track_pid() { echo "$1" >> "$PID_DIR/all.pids"; }
SKIPPED=()

wait_http() { local url="$1" tries="${2:-40}" i code
  for ((i=0;i<tries;i++)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "$url" 2>/dev/null)
    [[ "$code" =~ ^(200|204|401|403)$ ]] && return 0
    sleep 1
  done; return 1; }

wait_ready() { local name="$1" base="$2" i code
  for ((i=0;i<75;i++)); do
    for path in /readyz /healthz /health /api/v1/health; do
      code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "${base}${path}" 2>/dev/null)
      [[ "$code" =~ ^(200|204)$ ]] && { ok "$name ready (${path} ${code})"; return 0; }
    done; sleep 1
  done
  warn "$name did not become ready; tail log:"; tail -20 "$LOG_DIR/${name}.log" 2>/dev/null; return 1; }

psql_q() { PGPASSWORD=windrose_dev psql -h localhost -U windrose "$@"; }

build_go() { # dir binname subpath
  [ "$SKIP_BUILD" = 1 ] && [ -x "$BIN_DIR/$2" ] && { ok "reuse $2"; return; }
  say "build $2"; ( cd "$REPO_DIR/services/$1" && go build -o "$BIN_DIR/$2" ./"$3" ) || die "build $2 failed"; }

# Launch a service so it SURVIVES up.sh exiting: spawn.py os.setsid()'s the
# child into its own session (like `docker compose up -d`), so it is not torn
# down with the parent's process group. execvp preserves the pid we track.
SPAWN="$LOCAL_DIR/spawn.py"
boot() { local name="$1"; shift
  python3 "$SPAWN" "$LOG_DIR/${name}.log" "$@" &
  local pid=$!; disown "$pid" 2>/dev/null || true
  track_pid "$pid"; echo "$pid" > "$PID_DIR/${name}.pid"; }

kill_stale() {
  say "freeing ports / killing any stale windrose processes"
  if [ -f "$PID_DIR/all.pids" ]; then
    while read -r p; do [ -n "$p" ] && kill "$p" 2>/dev/null; done < "$PID_DIR/all.pids"
  fi
  pkill -f 'e2e/run/bin/' 2>/dev/null
  pkill -f 'uvicorn app.main:app' 2>/dev/null
  pkill -f 'http.server 8300' 2>/dev/null
  pkill -f 'tsx .*bff-graphql' 2>/dev/null; pkill -f 'next dev' 2>/dev/null
  for port in 3000 4000 8085 8086 8300 8301 8302 8303 8304 8305 8306 8307 8308 8310 \
              8311 8312 8313 8314 8315 8316 8320 8321 8322 8323 8324; do
    local pid; pid=$(lsof -ti tcp:$port 2>/dev/null); [ -n "$pid" ] && kill $pid 2>/dev/null
  done
  sleep 1; : > "$PID_DIR/all.pids"
}

# ============================================================ PHASE 0 preflight
say "${BLD}PHASE 0${NC}  preflight"
command -v docker >/dev/null || die "docker not on PATH"
docker info >/dev/null 2>&1 || die "Docker daemon not running — start Docker Desktop and retry"
DOCK_MEM=$(docker info --format '{{.MemTotal}}' 2>/dev/null)
DOCK_GB=$(awk "BEGIN{printf \"%.1f\", ${DOCK_MEM:-0}/1024/1024/1024}")
if awk "BEGIN{exit !(${DOCK_GB} < 10)}"; then
  warn "Docker has only ${DOCK_GB}GB — infra is fine but give Docker >=10GB if services flake"
else ok "Docker memory ${DOCK_GB}GB"; fi
command -v go >/dev/null || die "go not on PATH"
command -v uv >/dev/null || die "uv not on PATH"
command -v node >/dev/null || die "node not on PATH (need node@20 at /opt/homebrew/opt/node@20/bin)"
corepack enable >/dev/null 2>&1; corepack prepare pnpm@9.15.9 --activate >/dev/null 2>&1
command -v pnpm >/dev/null || die "pnpm not available (corepack)"
# Ollama + models (pull if missing)
if ! curl -s -m3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  die "Ollama not reachable at $OLLAMA_URL — run 'ollama serve' (brew services start ollama)"
fi
for model in llama3.2:latest qwen2.5:0.5b nomic-embed-text; do
  if curl -s -m3 "$OLLAMA_URL/api/tags" | grep -q "$model"; then ok "Ollama has $model"
  else warn "Ollama missing $model — pulling"; ollama pull "$model" || die "ollama pull $model failed"; fi
done
kill_stale

# ============================================================ PHASE 1 infra
say "${BLD}PHASE 1${NC}  infra up + healthy"
( cd "$REPO_DIR" && docker compose -f deploy/docker-compose.dev.yml up -d >/dev/null 2>&1 ) \
  || warn "compose up returned nonzero (stray containers?)"
# wait for the health-critical services
for i in $(seq 1 60); do psql_q -d postgres -tc 'select 1' >/dev/null 2>&1 && break; sleep 1; done
psql_q -d postgres -tc 'select 1' >/dev/null 2>&1 || die "postgres not reachable after 60s"
redis-cli -h localhost ping >/dev/null 2>&1 || warn "redis-cli missing (non-fatal)"
# OpenSearch is a JVM and boots slowly (30-60s+, worse on memory-constrained
# Docker), while `docker compose up -d` returns immediately — so a single-shot
# check races the boot and fatally fails `make up`. Retry like postgres does.
wait_http "$OPENSEARCH_URL/_cluster/health" 90 || die "opensearch not reachable after 90s"
curl -s -m5 "$MLFLOW_URL/health" >/dev/null 2>&1 || curl -s -m5 "$MLFLOW_URL/" >/dev/null 2>&1 || warn "mlflow not confirmed"
nc -z localhost 7233 2>/dev/null && ok "temporal 7233 open" || warn "temporal 7233 not open (agent HITL degraded)"
nc -z localhost 9010 2>/dev/null && ok "clickhouse 9010 open" || warn "clickhouse 9010 not open (audit degraded)"
ok "infra reachable"

# Keycloak (real IdP) — relax the master-realm HTTPS requirement so identity's
# admin-cli password grant works over the local HTTP gateway; otherwise tenant
# provisioning fails at CreateKeycloakRealm with "HTTPS required" (dev-only).
for i in $(seq 1 40); do curl -s -m2 -o /dev/null http://localhost:8180/realms/master && break; sleep 2; done
if docker exec windrose-dev-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
     --server http://localhost:8080 --realm master --user admin --password admin >/dev/null 2>&1; then
  docker exec windrose-dev-keycloak-1 /opt/keycloak/bin/kcadm.sh update realms/master \
     -s sslRequired=NONE >/dev/null 2>&1 && ok "keycloak master-realm sslRequired=NONE (dev)" \
     || warn "keycloak sslRequired update failed (real-IdP invites may 403)"
else
  warn "keycloak admin login failed (real-IdP invites may 403)"
fi

# per-service databases + pgvector (all 22; audit self-bootstraps its own)
say "ensuring per-service databases + pgvector extensions"
for db in identity rbac tool_plane case_svc realtimehub dataset ingestion ai_gateway \
          agent_runtime memory pipeline experiment inference query semantic eval \
          chart usage notification pack; do
  psql_q -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    || psql_q -d postgres -c "CREATE DATABASE $db" >/dev/null
done
for db in tool_plane dataset ai_gateway agent_runtime memory semantic; do
  psql_q -d "$db" -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null 2>&1
done
ok "databases ready"

# ============================================================ PHASE 1b JWKS
say "${BLD}PHASE 1b${NC}  harness IdP: JWKS server (RS256, real JWKS every service verifies)"
"$PY" "$E2E/lib/common.py" jwks > "$E2E/jwks/jwks.json"
pkill -f 'http.server 8300' 2>/dev/null; sleep 0.3
python3 "$SPAWN" "$LOG_DIR/jwks.log" bash -c "cd '$E2E/jwks' && exec '$PY' -m http.server '$WR_JWKS_PORT' --bind 127.0.0.1" &
disown $! 2>/dev/null || true; track_pid $!
wait_http "$WR_JWKS_URL" 10 || die "JWKS server did not start"
ok "JWKS serving harness public key at $WR_JWKS_URL"

# ============================================================ PHASE 2 services
# Reuse the e2e harness boot functions verbatim (money-path + retrain tail),
# then boot the remaining platform services (query/semantic/eval/chart/usage/
# audit/notification), then bff + ui.
source "$E2E/boot_services.sh"
source "$E2E/seed.sh"

say "${BLD}PHASE 2${NC}  migrate + boot all services (each wired to real infra + peers)"
boot_all                 # identity, rbac, realtime, case, ingestion, dataset, memory,
                         # ai-gateway, tool-plane(registry+gateway), agent-runtime,
                         # pipeline, experiment, inference  (+ provisions TENANT_ID, VKEY)
[ -n "${TENANT_ID:-}" ] || die "boot_all did not provision a tenant"

# Register inference-service's inference.submit write-proposal tool in tool-
# plane (idempotent: registry POSTs no-op/reuse on conflict, mcp_backends is
# an upsert) so an approved agent-runtime proposal federates to a real batch-
# inference job instead of stopping at the gateway. Both tool-plane and
# inference-service are up by this point (boot_all's last step is
# start_inference).
( cd "$E2E" && "$PY" lib/seed.py inference_tool "$TENANT_ID" ) 2>&1 | tee "$LOG_DIR/seed_inference_tool.log"

# Register ingestion-service's ingestion.create write-proposal tool in tool-
# plane (idempotent, same recipe as inference_tool above) so an approved
# agent-runtime onboarding proposal federates to a real ingestion job instead
# of stopping at the gateway. ingestion-service is up well before this point
# (boot_all starts it early, alongside identity/rbac/case).
( cd "$E2E" && "$PY" lib/seed.py ingestion_tool "$TENANT_ID" ) 2>&1 | tee "$LOG_DIR/seed_ingestion_tool.log"

boot_platform_extra      # query, semantic, chart, usage, audit, notification, eval

# Register chart-service's chart.dashboard.create write-proposal tool in
# tool-plane (idempotent, same recipe as inference_tool/ingestion_tool above)
# so an approved dashboard-designer proposal federates to a real dashboard+
# charts create instead of stopping at the gateway. chart-service is up by
# this point (boot_platform_extra's start_chart just ran).
( cd "$E2E" && "$PY" lib/seed.py chart_dashboard_tool "$TENANT_ID" ) 2>&1 | tee "$LOG_DIR/seed_chart_dashboard_tool.log"

# BRD 56 inc2: register the dataset.entity.merge write-proposal tool + point
# tool-plane's mcp_backends at dataset-service's facade, so an approved steward
# entity-merge proposal federates to a real confirm-merge (four-eyes, ER-FR-030).
( cd "$E2E" && "$PY" lib/seed.py entity_merge_tool "$TENANT_ID" ) 2>&1 | tee "$LOG_DIR/seed_entity_merge_tool.log"

# ---- bff-graphql (Node) ----
start_bff() {
  say "install + boot bff-graphql (Apollo, verifies harness JWKS, forwards bearer)"
  ( cd "$REPO_DIR/services/bff-graphql" && pnpm install --prefer-offline ) >> "$LOG_DIR/bff.log" 2>&1 \
    || warn "bff pnpm install returned nonzero"
  boot bff env PATH="$PATH" \
    PORT="$PORT_BFF" NODE_ENV=development VERIFY_JWT=true \
    JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    IDENTITY_URL="$IDENTITY_URL" DATASET_URL="$DATASET_URL" CASE_URL="$CASE_URL" \
    CHART_URL="$CHART_URL" USAGE_URL="$USAGE_URL" EXPERIMENT_URL="$EXPERIMENT_URL" \
    INFERENCE_URL="$INFERENCE_URL" \
    AGENT_RUNTIME_URL="$AGENT_RUNTIME_URL" RBAC_URL="$RBAC_URL" REALTIME_HUB_URL="$REALTIME_URL" \
    INGESTION_URL="$INGESTION_URL" PIPELINE_URL="$PIPELINE_URL" AUDIT_URL="$AUDIT_URL" \
    PACK_URL="$PACK_URL" \
    bash -c "cd '$REPO_DIR/services/bff-graphql' && exec pnpm start"
  wait_ready bff "$BFF_URL" || { warn "bff-graphql not ready"; SKIPPED+=("bff-graphql"); return 1; }
}
start_bff

# ---- seed (before ui so personas.json + tenant data exist) ----
# Platform layer (tenant + four RBAC-gated personas, no vertical data) always
# runs when seeding is on; the claims-vertical demo layer is additive and
# skippable independently (--platform-only), per Rule 3/Rule 4.
if [ "$SKIP_SEED" = 0 ]; then
  say "${BLD}PHASE 3${NC}  seeding the platform (tenant + four RBAC-gated personas)"
  ( cd "$LOCAL_DIR" && "$PY" seed_platform.py ) 2>&1 | tee "$LOG_DIR/seed_platform.log"
  if [ "$PLATFORM_ONLY" = 0 ]; then
    say "${BLD}PHASE 3b${NC} seeding the claims-vertical demo (real APIs; cases in queue + pending proposal)"
    ( cd "$LOCAL_DIR" && "$PY" seed_claims_demo.py ) 2>&1 | tee "$LOG_DIR/seed_claims_demo.log"
  else
    warn "skipping claims-vertical demo seed (--platform-only)"
  fi
else
  warn "skipping all seeding (--skip-seed)"
fi

# ---- ui-web (Next.js) ----
start_ui() {
  say "install + boot ui-web (Next.js; dev IdP signs with the harness key)"
  ( cd "$REPO_DIR/services/ui-web" && pnpm install --prefer-offline ) >> "$LOG_DIR/ui.log" 2>&1 \
    || warn "ui pnpm install returned nonzero"
  local personas="{}"
  [ -f "$RUN_DIR/personas.json" ] && personas="$(cat "$RUN_DIR/personas.json")"
  local privjwk pubjwk
  privjwk="$("$PY" "$E2E/lib/common.py" jwk_private)"
  pubjwk="$("$PY" "$E2E/lib/common.py" jwk_public)"
  boot ui env PATH="$PATH" \
    AUTH_MODE=dev \
    JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
    DEV_JWT_PRIVATE_JWK="$privjwk" DEV_JWT_PUBLIC_JWK="$pubjwk" \
    WINDROSE_PERSONAS="$personas" \
    BFF_URL="${BFF_URL}/graphql" \
    REALTIME_HUB_URL="$REALTIME_URL" NEXT_PUBLIC_REALTIME_HUB_URL="$REALTIME_URL" \
    AGENT_RUNTIME_URL="$AGENT_RUNTIME_URL" \
    bash -c "cd '$REPO_DIR/services/ui-web' && exec pnpm exec next dev -p $PORT_UI"
  # ui login page is the readiness signal (public route -> 200)
  wait_http "$UI_URL/login" 90 || { warn "ui-web did not serve /login"; SKIPPED+=("ui-web"); return 1; }
  ok "ui-web serving at $UI_URL"
}
start_ui

# ============================================================ reconcile
# The rbac permissions projection lives in Redis and the Go services read it
# directly (no synchronous fallback on a miss), so a cold Redis / restart would
# otherwise 403 every request until each tenant is rebuilt by hand. Self-heal
# all tenants now so a fresh boot is immediately usable. Idempotent + non-fatal.
if [ "$PLATFORM_ONLY" = 0 ]; then
  say "reconciling rbac projections (self-heal after restart)"
  ( cd "$LOCAL_DIR" && ./reconcile.sh ) 2>&1 | tee "$LOG_DIR/reconcile.log" \
    || warn "reconcile reported errors (see $LOG_DIR/reconcile.log)"
fi

# ============================================================ banner
# accurate native-service RSS (includes uvicorn workers / next-server children,
# which are not in the tracked-pid list)
RAM_MB=$(ps -Ao rss,command | grep -E 'e2e/run/bin/|uvicorn app.main|next-server|next dev|bff-graphql|http.server 8300' \
         | grep -v grep | awk '{s+=$1} END{printf "%d", s/1024}')
echo
echo "${GRN}${BLD}========================================================================${NC}"
echo "${GRN}${BLD}  Windrose is UP — open it in your browser${NC}"
echo "${GRN}${BLD}========================================================================${NC}"
echo
echo "  ${BLD}UI${NC}            ${BLU}$UI_URL${NC}"
echo "  ${BLD}GraphQL BFF${NC}   $BFF_URL/graphql"
echo
echo "  ${BLD}Log in as any persona (password: ${GRN}demo${NC}${BLD}):${NC}"
echo "     adjuster@demo.windrose       — triage the claims queue, approve a proposal"
echo "     manager@demo.windrose        — oversee cases + dashboards"
echo "     datascientist@demo.windrose  — datasets, experiments, promoted model"
echo "     admin@demo.windrose          — everything"
echo
if [ "$PLATFORM_ONLY" = 0 ]; then
  echo "  ${BLD}What to try:${NC}"
  echo "     Cases -> open a claim (e.g. the duplicate-invoice one from Zürich Ré)"
  echo "     -> Copilot triages it -> approve the proposal in the Inbox"
  echo "     -> the correction feeds the learning loop (retrain -> promoted model)"
else
  echo "  ${BLD}Platform-only boot${NC} — no vertical demo data seeded (--platform-only)."
  echo "     Log in as admin@demo.windrose and use Data > Upload + the semantic-model"
  echo "     and chart builders to onboard a use case by hand."
fi
echo
echo "  ${BLD}For the curious (infra consoles):${NC}"
echo "     Temporal UI   http://localhost:8233"
echo "     MLflow        $MLFLOW_URL"
echo "     MinIO console http://localhost:9001   (windrose / windrose_dev)"
echo "     Keycloak      http://localhost:8180"
echo
if [ "${#SKIPPED[@]}" -gt 0 ]; then
  echo "  ${YLW}${BLD}Degraded — these did not come up:${NC} ${SKIPPED[*]}"
  echo "     (see deploy/e2e/logs/*.log)"
  echo
fi
echo "  approx service RAM footprint: ${RAM_MB:-?} MB (native procs; infra is in Docker)"
echo "  stop everything with:  ${BLD}make down${NC}   (add ARGS=--infra to stop Docker too)"
echo "${GRN}${BLD}========================================================================${NC}"
echo
say "services are running in the background; this shell can be closed. 'make down' to stop."
