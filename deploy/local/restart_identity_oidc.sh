#!/usr/bin/env bash
# Restart identity-service + ui-web with the generic OIDC login enabled (BYO-P4),
# pointed at the local Keycloak `windrose` realm (run keycloak_bootstrap.sh
# first). OIDC_TENANT_ID must be a real tenant, so this reads TENANT_ID from the
# running platform's context.env rather than the initial boot (where no tenant
# exists yet). The dev/persona login stays on (AUTH_MODE=dev) so nothing else
# breaks — the SSO button is additive.
#
# Usage: deploy/local/restart_identity_oidc.sh
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"
[ -f "$E2E/run/context.env" ] && source "$E2E/run/context.env"
[ -n "${TENANT_ID:-}" ] || { echo "TENANT_ID not set — is the platform up?" >&2; exit 1; }

export PATH="/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:$PATH"
LOCAL_DIR="$(pwd)"
mkdir -p "$LOG_DIR" "$BIN_DIR" "$PID_DIR"
SPAWN="$LOCAL_DIR/spawn.py"
RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }
die()  { echo "${RED}FATAL:${NC} $*" >&2; exit 1; }
track_pid() { echo "$1" >> "$PID_DIR/all.pids"; }
wait_ready() { local name="$1" base="$2" i code
  for ((i=0;i<90;i++)); do
    for path in /readyz /healthz /health /api/v1/health; do
      code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "${base}${path}" 2>/dev/null)
      [[ "$code" =~ ^(200|204)$ ]] && { ok "$name ready (${path} ${code})"; return 0; }
    done; sleep 1
  done
  warn "$name did not become ready"; tail -20 "$LOG_DIR/${name}.log" 2>/dev/null; return 1; }
build_go() { say "build $2"; ( cd "$REPO_DIR/services/$1" && go build -o "$BIN_DIR/$2" ./"$3" ) || die "build $2 failed"; }
boot() { local name="$1"; shift
  python3 "$SPAWN" "$LOG_DIR/${name}.log" "$@" &
  local pid=$!; disown "$pid" 2>/dev/null || true
  track_pid "$pid"; echo "$pid" > "$PID_DIR/${name}.pid"; }

# The OIDC config for the local Keycloak windrose realm (keycloak_bootstrap.sh).
export OIDC_ISSUER="${OIDC_ISSUER:-http://localhost:8180/realms/windrose}"
export OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-windrose-web}"
export OIDC_TENANT_ID="$TENANT_ID"
export OIDC_REDIRECT_URI="http://localhost:3000/api/auth/callback"
export NEXT_PUBLIC_OIDC_ENABLED="true"
export IDENTITY_URL

source "$E2E/boot_services.sh"

say "restarting identity-service with OIDC login (issuer=$OIDC_ISSUER tenant=$OIDC_TENANT_ID)"
stale="$(lsof -ti "tcp:$PORT_IDENTITY" -sTCP:LISTEN 2>/dev/null || true)"
[ -n "$stale" ] && { kill -9 $stale 2>/dev/null; sleep 1; }
start_identity
ok "identity-service restarted (OIDC enabled)"

say "restarting ui-web with the SSO button + OIDC routes"
bash "$LOCAL_DIR/restart_ui.sh"
ok "done — visit http://localhost:3000/login and click 'Sign in with SSO'"
