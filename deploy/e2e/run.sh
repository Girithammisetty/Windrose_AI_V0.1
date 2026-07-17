#!/usr/bin/env bash
# Windrose repo-level end-to-end proof. Boots the full real stack (infra +
# every money-path service, no fakes in the path) and drives the insurance
# claims triage-and-governance journey with real evidence at each step.
#
# Usage: deploy/e2e/run.sh [--down] [--no-teardown] [--skip-build]
set -uo pipefail
cd "$(dirname "$0")"
source ./config.env

DOWN_INFRA=0; NO_TEARDOWN=0; SKIP_BUILD=0
for a in "$@"; do case "$a" in
  --down) DOWN_INFRA=1;;
  --no-teardown) NO_TEARDOWN=1;;
  --skip-build) SKIP_BUILD=1;;
esac; done

mkdir -p "$LOG_DIR" "$BIN_DIR" "$PID_DIR"

# free our ports / kill any prior e2e processes from a previous run
kill_stale() {
  if [ -f "$PID_DIR/all.pids" ]; then
    while read -r p; do [ -n "$p" ] && kill "$p" 2>/dev/null; done < "$PID_DIR/all.pids"
  fi
  pkill -f 'e2e/run/bin/' 2>/dev/null
  pkill -f 'uvicorn app.main:app' 2>/dev/null
  pkill -f 'http.server 8300' 2>/dev/null
  for port in 8300 8301 8302 8303 8304 8305 8306 8307 8308 8310 8311 8312 8313 8314 8315 8316; do
    local pid; pid=$(lsof -ti tcp:$port 2>/dev/null)
    [ -n "$pid" ] && kill $pid 2>/dev/null
  done
  sleep 1
}
kill_stale
: > "$PID_DIR/all.pids"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }
die()  { echo "${RED}FATAL:${NC} $*" >&2; teardown; exit 1; }

track_pid() { echo "$1" >> "$PID_DIR/all.pids"; }

teardown() {
  [ "$NO_TEARDOWN" = 1 ] && { warn "leaving services running (--no-teardown)"; return; }
  say "tearing down services"
  if [ -f "$PID_DIR/all.pids" ]; then
    while read -r p; do [ -n "$p" ] && kill "$p" 2>/dev/null; done < "$PID_DIR/all.pids"
  fi
  pkill -f 'e2e/run/bin/' 2>/dev/null
  if [ "$DOWN_INFRA" = 1 ]; then say "stopping infra"; (cd "$REPO_DIR" && docker compose -f deploy/docker-compose.dev.yml down); fi
}
trap teardown EXIT

wait_http() { # url [tries]
  local url="$1" tries="${2:-40}" i code
  for ((i=0;i<tries;i++)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "$url" 2>/dev/null)
    [[ "$code" =~ ^(200|204)$ ]] && return 0
    sleep 1
  done
  return 1
}
wait_ready() { # name base_url  (probes /readyz then /healthz then /health)
  local name="$1" base="$2"
  for path in /readyz /healthz /health /api/v1/health; do
    if wait_http "${base}${path}" 3 >/dev/null 2>&1; then :; fi
  done
  local i code
  for ((i=0;i<60;i++)); do
    for path in /readyz /healthz /health /api/v1/health; do
      code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "${base}${path}" 2>/dev/null)
      [[ "$code" =~ ^(200|204)$ ]] && { ok "$name ready (${path} ${code})"; return 0; }
    done
    sleep 1
  done
  warn "$name did not become ready; tail log:"; tail -25 "$LOG_DIR/${name}.log" 2>/dev/null
  return 1
}

psql_q() { PGPASSWORD=windrose_dev psql -h localhost -U windrose "$@"; }

########################################  PHASE 0: preflight  ############################
say "PHASE 0  preflight: infra + toolchain + Ollama"
(cd "$REPO_DIR" && docker compose -f deploy/docker-compose.dev.yml up -d >/dev/null 2>&1) || warn "compose up returned nonzero (stray containers?)"
command -v go >/dev/null || die "go not on PATH"
command -v uv >/dev/null || die "uv not on PATH"
curl -s -m3 "$OLLAMA_URL/api/tags" | grep -q 'qwen2.5:0.5b' || die "Ollama qwen2.5:0.5b not reachable at $OLLAMA_URL"
curl -s -m3 "$OLLAMA_URL/api/tags" | grep -q 'nomic-embed-text' || die "Ollama nomic-embed-text not present"
ok "Ollama has qwen2.5:0.5b + nomic-embed-text"
for probe in "postgres:$(psql_q -d postgres -tc 'select 1' 2>/dev/null | tr -d ' ')" ; do :; done
psql_q -d postgres -tc 'select 1' >/dev/null 2>&1 || die "postgres not reachable"
redis-cli -h localhost ping >/dev/null 2>&1 || warn "redis-cli missing (non-fatal)"
curl -s -m3 "$OPENSEARCH_URL/_cluster/health" >/dev/null || die "opensearch not reachable"
curl -s -m3 "$OPA_URL/health" >/dev/null || warn "opa /health not 200 (may still serve)"
nc -z localhost 7233 2>/dev/null && ok "temporal 7233 open" || warn "temporal 7233 not open (agent-runtime HITL will fall back)"
ok "infra reachable"

# databases + pgvector
say "ensuring per-service databases + pgvector"
for db in identity rbac tool_plane case_svc realtimehub dataset ingestion ai_gateway agent_runtime memory pipeline experiment inference; do
  psql_q -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || psql_q -d postgres -c "CREATE DATABASE $db" >/dev/null
done
for db in tool_plane dataset ai_gateway agent_runtime memory; do
  psql_q -d "$db" -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null 2>&1
done
ok "databases ready"

########################################  PHASE 1: JWKS (harness IdP)  ###################
say "PHASE 1  harness IdP: JWKS server"
"$PY" lib/common.py jwks > jwks/jwks.json
pkill -f 'http.server 8300' 2>/dev/null; sleep 0.3
( cd jwks && exec "$PY" -m http.server "$WR_JWKS_PORT" --bind 127.0.0.1 ) > "$LOG_DIR/jwks.log" 2>&1 &
track_pid $!
wait_http "$WR_JWKS_URL" 10 || die "JWKS server did not start"
ok "JWKS serving harness public key at $WR_JWKS_URL"

########################################  helpers to boot go/py services  ###############
build_go() { # dir binname
  [ "$SKIP_BUILD" = 1 ] && [ -x "$BIN_DIR/$2" ] && { ok "reuse $2"; return; }
  say "build $2"; ( cd "$REPO_DIR/services/$1" && go build -o "$BIN_DIR/$2" ./"$3" ) || die "build $2 failed"
}
boot() { # name logfile  -- then env+cmd via remaining args (run in service dir already set by caller)
  local name="$1"; shift
  ( "$@" ) > "$LOG_DIR/${name}.log" 2>&1 &
  local pid=$!; track_pid "$pid"; echo "$pid" > "$PID_DIR/${name}.pid"
}

source ./boot_services.sh   # defines start_<svc> functions using the env above
source ./seed.sh            # defines seed_* functions (cells, signing key, rbac, tools, agent, ai-gw)

boot_all
run_driver
E2E_RC=$?
exit $E2E_RC
