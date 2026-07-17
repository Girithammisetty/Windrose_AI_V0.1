#!/usr/bin/env bash
# Restart experiment-service in isolation (e.g. after a code change) against
# the already-running platform. Mirrors boot_services.sh::start_experiment()
# exactly (same env, same uv-run boot) — companion to restart_agent_runtime.sh.
#
# Usage: deploy/local/restart_experiment.sh
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"

export PATH="/opt/homebrew/bin:$PATH"
LOCAL_DIR="$(pwd)"
mkdir -p "$LOG_DIR" "$PID_DIR"
SPAWN="$LOCAL_DIR/spawn.py"

GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }
track_pid() { echo "$1" >> "$PID_DIR/all.pids"; }

wait_ready() { local base="$1" i code
  for ((i=0;i<40;i++)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "$base/healthz" 2>/dev/null)
    [[ "$code" == "200" ]] && return 0
    sleep 1
  done; return 1; }

say "stopping the current experiment-service process"
stale="$(lsof -ti "tcp:$PORT_EXPERIMENT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$stale" ]; then
  kill "$stale" 2>/dev/null; sleep 1
  still="$(lsof -ti "tcp:$PORT_EXPERIMENT" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$still" ] && kill -9 "$still" 2>/dev/null
  sleep 1
else
  warn "no process was holding port $PORT_EXPERIMENT"
fi

say "booting experiment-service from this tree"
export EXP_DATABASE_URL="postgresql+asyncpg://experiment_app:experiment_app@localhost:5432/experiment"
export EXP_MIGRATE_URL="postgresql+psycopg://windrose:windrose_dev@localhost:5432/experiment"
export EXP_USE_REAL_ADAPTERS=true EXP_ENV=dev
export EXP_MLFLOW_TRACKING_URI="$MLFLOW_URL"
export EXP_KAFKA_BOOTSTRAP_SERVERS="$KAFKA_BROKERS" EXP_REDIS_URL="$REDIS_URL" EXP_OPA_URL="$OPA_URL"
export EXP_S3_ENDPOINT_URL="$S3_ENDPOINT" EXP_S3_ACCESS_KEY=windrose EXP_S3_SECRET_KEY=windrose_dev
export EXP_S3_REGION=us-east-1
export EXP_JWKS_URL="$WR_JWKS_URL" EXP_JWT_ISSUER="$WR_ISS" EXP_JWT_AUDIENCE="$WR_AUD"
export EXP_RBAC_URL="$RBAC_URL" EXP_REGISTER_SIGNING_KEY_PEM="$(cat "$E2E/keys/idp_private.pem")"
export EXP_REGISTER_SIGNING_KID="e2e-harness-key-1" EXP_REGISTER_TENANT_ID="${TENANT_ID:-}"

python3 "$SPAWN" "$LOG_DIR/experiment.log" bash -c \
  "cd '$REPO_DIR/services/experiment-service' && exec uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT_EXPERIMENT" &
pid=$!; disown "$pid" 2>/dev/null || true
track_pid "$pid"; echo "$pid" > "$PID_DIR/experiment.pid"

wait_ready "$EXPERIMENT_URL" || { warn "experiment-service did not become ready"; exit 1; }
ok "experiment-service serving at $EXPERIMENT_URL"
