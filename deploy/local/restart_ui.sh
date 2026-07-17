#!/usr/bin/env bash
# Restart ui-web in isolation (e.g. after regenerating personas.json) against
# the already-running platform, without a full deploy/local/up.sh re-boot.
# Mirrors up.sh's own start_ui() exactly.
#
# Usage: deploy/local/restart_ui.sh
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"

export PATH="/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:$PATH"
LOCAL_DIR="$(pwd)"
RUN_DIR="$LOCAL_DIR/run"
PY="$E2E/.venv/bin/python3"
SPAWN="$LOCAL_DIR/spawn.py"
mkdir -p "$LOG_DIR" "$BIN_DIR" "$PID_DIR" "$RUN_DIR"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }
track_pid() { echo "$1" >> "$PID_DIR/all.pids"; }

wait_http() { local url="$1" tries="${2:-40}" i code
  for ((i=0;i<tries;i++)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "$url" 2>/dev/null)
    [[ "$code" =~ ^(200|204|401|403)$ ]] && return 0
    sleep 1
  done; return 1; }

boot() { local name="$1"; shift
  python3 "$SPAWN" "$LOG_DIR/${name}.log" "$@" &
  local pid=$!; disown "$pid" 2>/dev/null || true
  track_pid "$pid"; echo "$pid" > "$PID_DIR/${name}.pid"; }

say "stopping the current ui-web process"
stale="$(lsof -ti "tcp:$PORT_UI" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$stale" ]; then
  kill "$stale" 2>/dev/null; sleep 1
  still="$(lsof -ti "tcp:$PORT_UI" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$still" ] && kill -9 "$still" 2>/dev/null
  sleep 1
else
  warn "no process was holding port $PORT_UI"
fi

say "booting ui-web with the current personas.json"
personas="{}"
[ -f "$RUN_DIR/personas.json" ] && personas="$(cat "$RUN_DIR/personas.json")"
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
  IDENTITY_URL="${IDENTITY_URL:-http://localhost:8301}" \
  OIDC_ISSUER="${OIDC_ISSUER:-}" OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-}" \
  OIDC_REDIRECT_URI="${OIDC_REDIRECT_URI:-}" NEXT_PUBLIC_OIDC_ENABLED="${NEXT_PUBLIC_OIDC_ENABLED:-}" \
  bash -c "cd '$REPO_DIR/services/ui-web' && exec pnpm exec next dev -p $PORT_UI"

wait_http "$UI_URL/login" 90 || { warn "ui-web did not serve /login"; exit 1; }
ok "ui-web serving at $UI_URL"
