#!/usr/bin/env bash
# Restart bff-graphql in isolation against the already-running platform,
# without a full deploy/local/up.sh re-boot. Mirrors up.sh's start_bff()
# exactly (same env recipe); companion to restart_ui.sh.
#
# Usage: deploy/local/restart_bff.sh
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"

export PATH="/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:$PATH"
LOCAL_DIR="$(pwd)"
PID_DIR="${PID_DIR:-$LOCAL_DIR/run/pids}"
mkdir -p "$LOG_DIR" "$PID_DIR"

GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }

wait_http() { local url="$1" tries="${2:-40}" i code
  for ((i=0;i<tries;i++)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "$url" 2>/dev/null)
    [[ "$code" =~ ^(200|204|400|401|403|405)$ ]] && return 0
    sleep 1
  done; return 1; }

boot() { local name="$1"; shift
  python3 "$LOCAL_DIR/spawn.py" "$LOG_DIR/${name}.log" "$@" &
  local pid=$!; disown "$pid" 2>/dev/null || true
  echo "$pid" > "$PID_DIR/${name}.pid"; }

say "stopping the current bff-graphql process"
stale="$(lsof -ti "tcp:$PORT_BFF" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$stale" ]; then
  kill "$stale" 2>/dev/null; sleep 1
  still="$(lsof -ti "tcp:$PORT_BFF" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$still" ] && kill -9 "$still" 2>/dev/null
  sleep 1
else
  warn "no process was holding port $PORT_BFF"
fi

say "booting bff-graphql from this tree"
boot bff env PATH="$PATH" \
  PORT="$PORT_BFF" NODE_ENV=development VERIFY_JWT=true \
  JWKS_URL="$WR_JWKS_URL" JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
  IDENTITY_URL="$IDENTITY_URL" DATASET_URL="$DATASET_URL" CASE_URL="$CASE_URL" \
  CHART_URL="$CHART_URL" USAGE_URL="$USAGE_URL" EXPERIMENT_URL="$EXPERIMENT_URL" \
  INFERENCE_URL="$INFERENCE_URL" \
  AGENT_RUNTIME_URL="$AGENT_RUNTIME_URL" RBAC_URL="$RBAC_URL" REALTIME_HUB_URL="$REALTIME_URL" \
  INGESTION_URL="$INGESTION_URL" PIPELINE_URL="$PIPELINE_URL" AUDIT_URL="$AUDIT_URL" \
  bash -c "cd '$REPO_DIR/services/bff-graphql' && exec pnpm start"

wait_http "$BFF_URL/graphql" 60 || { warn "bff-graphql did not come up"; exit 1; }
ok "bff-graphql serving at $BFF_URL"
