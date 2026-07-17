#!/usr/bin/env bash
# Restart agent-runtime in isolation (e.g. after a code change) against the
# already-running platform, WITHOUT the classic "stale key/process drift"
# footgun: this always re-seeds a fresh ai-gateway virtual key and passes it
# straight to the new process, rather than reusing whatever secret happened
# to be sitting in a shell/env from a previous boot. See
# project_windrose docs / KEY_INVALID incident (2026-07-17) for the failure
# mode this replaces (a manually-restarted process holding a virtual-key
# secret whose hash no longer matches any row in ai_gateway.virtual_keys).
#
# Usage: deploy/local/restart_agent_runtime.sh
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

boot() { local name="$1"; shift
  python3 "$SPAWN" "$LOG_DIR/${name}.log" "$@" &
  local pid=$!; disown "$pid" 2>/dev/null || true
  track_pid "$pid"; echo "$pid" > "$PID_DIR/${name}.pid"; }

source "$E2E/boot_services.sh"
source "$E2E/seed.sh"

# TENANT_ID (seed_ai_gateway needs it) — the already-running platform's tenant,
# stashed by seed.sh's own provision_tenant() on first boot.
[ -f "$E2E/run/context.env" ] && source "$E2E/run/context.env"
[ -n "${TENANT_ID:-}" ] || die "TENANT_ID not set — is the platform actually up? (deploy/e2e/run/context.env missing)"

say "stopping the current agent-runtime process"
stale="$(lsof -ti "tcp:$PORT_AGENT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$stale" ]; then
  kill "$stale" 2>/dev/null; sleep 1
  still="$(lsof -ti "tcp:$PORT_AGENT" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$still" ] && kill -9 "$still" 2>/dev/null
  sleep 1
else
  warn "no process was holding port $PORT_AGENT"
fi

# Multi-tenant default (BRD 52): boot WITHOUT a static virtual key so the
# container's TenantVirtualKeyProvider mints a per-tenant key at call time —
# a single pinned key only matches ONE tenant and 401s every other tenant's
# LLM call on this shared process. Set RESTART_STATIC_VKEY=1 to restore the
# old single-tenant-pinned behavior (the original stale-key incident fix).
if [ "${RESTART_STATIC_VKEY:-0}" = "1" ]; then
  say "re-seeding a fresh ai-gateway virtual key and starting agent-runtime (single-tenant pin)"
  start_agent_runtime "$(seed_ai_gateway)"
  ok "agent-runtime restarted with a freshly-seeded, guaranteed-in-sync virtual key"
else
  say "starting agent-runtime with per-tenant ai-gateway key minting (multi-tenant)"
  start_agent_runtime ""
  ok "agent-runtime restarted; virtual keys mint per tenant at call time"
fi
