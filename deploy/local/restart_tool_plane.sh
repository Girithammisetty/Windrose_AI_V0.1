#!/usr/bin/env bash
# Restart tool-plane (tool-registry + mcp-gateway) in isolation, e.g. after a
# code change, against the already-running platform. Mirrors up.sh's own
# start_tool_plane() exactly (rebuilds both Go binaries, then reboots both).
#
# Usage: deploy/local/restart_tool_plane.sh
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"

export PATH="/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:$PATH"
LOCAL_DIR="$(pwd)"
RUN_DIR="$LOCAL_DIR/run"
mkdir -p "$LOG_DIR" "$BIN_DIR" "$PID_DIR" "$RUN_DIR"
SPAWN="$LOCAL_DIR/spawn.py"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }
die()  { echo "${RED}FATAL:${NC} $*" >&2; exit 1; }
track_pid() { echo "$1" >> "$PID_DIR/all.pids"; }

wait_ready() { local name="$1" base="$2" i code
  for ((i=0;i<75;i++)); do
    for path in /readyz /healthz /health /api/v1/health; do
      code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "${base}${path}" 2>/dev/null)
      [[ "$code" =~ ^(200|204)$ ]] && { ok "$name ready (${path} ${code})"; return 0; }
    done; sleep 1
  done
  warn "$name did not become ready; tail log:"; tail -20 "$LOG_DIR/${name}.log" 2>/dev/null; return 1; }

build_go() { # dir binname subpath
  say "build $2"; ( cd "$REPO_DIR/services/$1" && go build -o "$BIN_DIR/$2" ./"$3" ) || die "build $2 failed"; }

boot() { local name="$1"; shift
  python3 "$SPAWN" "$LOG_DIR/${name}.log" "$@" &
  local pid=$!; disown "$pid" 2>/dev/null || true
  track_pid "$pid"; echo "$pid" > "$PID_DIR/${name}.pid"; }

source "$E2E/boot_services.sh"

[ -f "$E2E/run/context.env" ] && source "$E2E/run/context.env"
[ -n "${TENANT_ID:-}" ] || die "TENANT_ID not set — is the platform actually up? (deploy/e2e/run/context.env missing)"

say "stopping the current tool-registry + mcp-gateway processes"
for port in "$PORT_TOOLREG" "$PORT_GATEWAY"; do
  stale="$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$stale" ]; then
    kill "$stale" 2>/dev/null; sleep 1
    still="$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null || true)"
    [ -n "$still" ] && kill -9 "$still" 2>/dev/null
  else
    warn "no process was holding port $port"
  fi
done
sleep 1

say "rebuilding + starting tool-registry and mcp-gateway"
start_tool_plane
ok "tool-plane (tool-registry + mcp-gateway) restarted"
