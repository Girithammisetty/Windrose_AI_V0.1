#!/usr/bin/env bash
# Local observability alerting drill.
#
# Gap being closed: Prometheus/Alertmanager/Grafana have never actually run in
# this dev stack -- the alert rules in
# deploy/helm/datacern/templates/prometheusrule.yaml were only ever validated
# via `helm lint`/`helm template`, never against a live Prometheus
# rule-evaluation engine. This script proves DatacernHighErrorRate genuinely
# transitions inactive -> pending -> firing against real scraped metrics from
# a live service, using a throwaway Prometheus container (never added to
# docker-compose.dev.yml -- pure `docker run --rm`).
#
# What it does:
#   1. Renders the Helm PrometheusRule template into a plain rule file
#      (render_rules.py -- no hand-duplicated rule bodies).
#   2. Restarts notification-service (ONLY this service) with
#      CHAOS_ENDPOINTS_ENABLED=true so POST /internal/chaos/error produces a
#      real 500 (see internal/api/chaos.go) -- default is 404 (endpoint does
#      not exist), so this is required and reverted at the end.
#   3. Starts a throwaway `prom/prometheus` container on :9091 scraping the
#      live notification-service.
#   4. Drives continuous synthetic 500s so the job's 5xx ratio stays >1% for
#      the whole `for: 5m` window.
#   5. Polls /api/v1/rules every 15s (up to 6 minutes) and prints the observed
#      state transitions with real timestamps.
#   6. Tears down the drill Prometheus container and restarts
#      notification-service back to normal (CHAOS_ENDPOINTS_ENABLED unset).
#
# Usage: deploy/observability/drill.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
E2E_DIR="$REPO_ROOT/deploy/e2e"
PY_VENV="$E2E_DIR/.venv/bin/python"

# shellcheck source=/dev/null
source "$E2E_DIR/config.env"
if [ -f "$E2E_DIR/run/context.env" ]; then
  # shellcheck source=/dev/null
  source "$E2E_DIR/run/context.env"
fi
if [ -z "${TENANT_ID:-}" ]; then
  echo "FATAL: TENANT_ID not set (deploy/e2e/run/context.env missing) -- is the dev stack booted? (deploy/e2e/run.sh)" >&2
  exit 1
fi

DRILL_PROM_NAME="datacern-drill-prom"
DRILL_PROM_PORT=9091
RULES_POLL_INTERVAL=15
RULES_POLL_MAX_MINUTES=6
LOAD_INTERVAL=1   # seconds between synthetic-error POSTs while the drill runs

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[36m'; NC=$'\e[0m'
say()  { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}  ok${NC} $*"; }
warn() { echo "${YLW}  !!${NC} $*"; }
die()  { echo "${RED}FATAL:${NC} $*" >&2; cleanup; exit 1; }
ts()   { date '+%Y-%m-%dT%H:%M:%S%z'; }

LOAD_PID=""
PROM_STARTED=0
CHAOS_ENABLED_BY_US=0

cleanup() {
  if [ -n "$LOAD_PID" ] && kill -0 "$LOAD_PID" 2>/dev/null; then
    say "stopping synthetic-load generator (pid $LOAD_PID)"
    kill "$LOAD_PID" 2>/dev/null; wait "$LOAD_PID" 2>/dev/null
  fi
  if [ "$PROM_STARTED" = 1 ]; then
    say "tearing down drill Prometheus container ($DRILL_PROM_NAME)"
    docker rm -f "$DRILL_PROM_NAME" >/dev/null 2>&1 || true
  fi
  if [ "$CHAOS_ENABLED_BY_US" = 1 ]; then
    say "restoring notification-service to normal (CHAOS_ENDPOINTS_ENABLED unset)"
    restart_notification "false"
    verify_chaos_state "false" || warn "post-drill verification: chaos endpoint still reachable! Check $LOG_DIR/notification.log"
  fi
}
trap cleanup EXIT INT TERM

# ---- minimal boot-helpers compatible with deploy/e2e/boot_services.sh -----
# (deliberately NOT re-running the full run.sh; only reusing start_notification
# so this stays the single source of truth for notification-service's real
# boot env, no drift between the e2e harness and this drill.)
SKIP_BUILD=0
mkdir -p "$LOG_DIR" "$BIN_DIR" "$PID_DIR"
build_go() { # dir binname path
  say "build $2"
  ( cd "$REPO_DIR/services/$1" && go build -o "$BIN_DIR/$2" ./"$3" ) || die "build $2 failed"
}
boot() { # name -- env+cmd
  local name="$1"; shift
  ( "$@" ) > "$LOG_DIR/${name}.log" 2>&1 &
  local pid=$!
  echo "$pid" >> "$PID_DIR/all.pids"
  echo "$pid" > "$PID_DIR/${name}.pid"
}
wait_http() {
  local url="$1" tries="${2:-40}" i code
  for ((i=0;i<tries;i++)); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "$url" 2>/dev/null)
    [[ "$code" =~ ^(200|204)$ ]] && return 0
    sleep 1
  done
  return 1
}
wait_ready() {
  local name="$1" base="$2" i code
  for ((i=0;i<60;i++)); do
    for path in /readyz /healthz; do
      code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "${base}${path}" 2>/dev/null)
      [[ "$code" =~ ^(200|204)$ ]] && { ok "$name ready (${path} ${code})"; return 0; }
    done
    sleep 1
  done
  warn "$name did not become ready; tail log:"; tail -25 "$LOG_DIR/${name}.log" 2>/dev/null
  return 1
}
SKIPPED=()
# shellcheck source=/dev/null
source "$E2E_DIR/boot_services.sh"   # defines start_notification()

restart_notification() { # "true" | "false"
  local chaos="$1"
  local stale; stale="$(lsof -ti "tcp:$PORT_NOTIFICATION" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$stale" ]; then
    say "stopping current notification-service (pid $stale) to restart with CHAOS_ENDPOINTS_ENABLED=$chaos"
    kill "$stale" 2>/dev/null
    for ((i=0;i<10;i++)); do kill -0 "$stale" 2>/dev/null || break; sleep 1; done
    kill -9 "$stale" 2>/dev/null || true
  fi
  export CHAOS_ENDPOINTS_ENABLED="$chaos"
  start_notification || die "notification-service failed to restart (chaos=$chaos)"
}

verify_chaos_state() { # "true" -> expect 500 ; "false" -> expect 404
  local want="$1" code
  code=$(curl -s -o /dev/null -w '%{http_code}' -m5 -X POST "${NOTIFICATION_URL}/internal/chaos/error")
  if [ "$want" = "true" ]; then
    [ "$code" = "500" ]
  else
    [ "$code" = "404" ]
  fi
}

########################################################################
say "PHASE 0  pre-flight"
if ! wait_http "${NOTIFICATION_URL}/healthz" 3; then
  die "notification-service not reachable at ${NOTIFICATION_URL}/healthz -- is the dev stack up? (deploy/e2e/run.sh)"
fi
ok "notification-service is up at $NOTIFICATION_URL"

say "PHASE 1  render Prometheus rule file from the Helm template (single source of truth)"
"$PY_VENV" "$SCRIPT_DIR/render_rules.py" || die "render_rules.py failed"
[ -f "$SCRIPT_DIR/rules.generated.yml" ] || die "rules.generated.yml not produced"
ok "rules.generated.yml is fresh"

say "PHASE 2  restart notification-service with CHAOS_ENDPOINTS_ENABLED=true"
restart_notification "true"
CHAOS_ENABLED_BY_US=1
wait_http "${NOTIFICATION_URL}/healthz" 20 || die "notification-service not healthy after chaos-mode restart"
if ! verify_chaos_state "true"; then
  die "POST /internal/chaos/error did not return 500 after restart -- chaos endpoint not reachable, aborting drill (see $LOG_DIR/notification.log)"
fi
ok "chaos endpoint live: POST ${NOTIFICATION_URL}/internal/chaos/error -> 500"

say "PHASE 3  start throwaway Prometheus (drill-only, not in docker-compose.dev.yml)"
docker rm -f "$DRILL_PROM_NAME" >/dev/null 2>&1 || true
docker run --rm -d --name "$DRILL_PROM_NAME" \
  -p "${DRILL_PROM_PORT}:9090" \
  -v "$SCRIPT_DIR:/etc/prometheus" \
  prom/prometheus --config.file=/etc/prometheus/prometheus.yml \
  >/dev/null || die "failed to start drill Prometheus container"
PROM_STARTED=1
wait_http "http://localhost:${DRILL_PROM_PORT}/-/ready" 30 || die "drill Prometheus did not become ready"
ok "drill Prometheus ready at http://localhost:${DRILL_PROM_PORT}"

say "checking notification-service scrape target is UP"
target_ok=0
for ((i=0;i<20;i++)); do
  health=$(curl -s "http://localhost:${DRILL_PROM_PORT}/api/v1/targets" | jq -r '.data.activeTargets[] | select(.labels.job=="notification-service") | .health' 2>/dev/null)
  [ "$health" = "up" ] && { target_ok=1; break; }
  sleep 2
done
[ "$target_ok" = 1 ] || warn "notification-service target not confirmed UP yet -- continuing anyway, will show in rule state"
ok "scrape target health: ${health:-unknown}"

say "PHASE 4  drive continuous synthetic 5xx load (every ${LOAD_INTERVAL}s) to keep the job's error ratio >1% for the whole for:5m window"
(
  while true; do
    curl -s -o /dev/null -m3 -X POST "${NOTIFICATION_URL}/internal/chaos/error"
    sleep "$LOAD_INTERVAL"
  done
) &
LOAD_PID=$!
ok "load generator running (pid $LOAD_PID)"

say "PHASE 5  polling DatacernHighErrorRate state every ${RULES_POLL_INTERVAL}s (up to ${RULES_POLL_MAX_MINUTES}m)"
echo
declare -a TRANSITIONS=()
last_state=""
deadline=$(( $(date +%s) + RULES_POLL_MAX_MINUTES * 60 ))
FINAL_STATE="unknown"
while [ "$(date +%s)" -lt "$deadline" ]; do
  resp=$(curl -s "http://localhost:${DRILL_PROM_PORT}/api/v1/rules")
  state=$(echo "$resp" | jq -r '.data.groups[].rules[] | select(.name=="DatacernHighErrorRate") | .state' 2>/dev/null)
  if [ -z "$state" ]; then
    warn "$(ts)  could not read DatacernHighErrorRate state from /api/v1/rules yet"
  elif [ "$state" != "$last_state" ]; then
    line="$(ts)  DatacernHighErrorRate: ${last_state:-<none>} -> ${state}"
    echo "${GRN}${line}${NC}"
    TRANSITIONS+=("$line")
    last_state="$state"
    FINAL_STATE="$state"
  else
    echo "$(ts)  DatacernHighErrorRate: ${state} (unchanged)"
    FINAL_STATE="$state"
  fi
  [ "$state" = "firing" ] && break
  sleep "$RULES_POLL_INTERVAL"
done
echo

say "PHASE 6  final rule detail"
curl -s "http://localhost:${DRILL_PROM_PORT}/api/v1/rules" | jq '.data.groups[].rules[] | select(.name=="DatacernHighErrorRate")'

echo
say "state transitions observed:"
if [ "${#TRANSITIONS[@]}" -eq 0 ]; then
  echo "  (none recorded -- state never changed from initial read)"
else
  for line in "${TRANSITIONS[@]}"; do echo "  $line"; done
fi
echo

if [ "$FINAL_STATE" = "firing" ]; then
  ok "PASS -- DatacernHighErrorRate reached firing"
  RESULT=0
else
  warn "FAIL -- DatacernHighErrorRate did not reach firing within ${RULES_POLL_MAX_MINUTES} minutes (final observed state: ${FINAL_STATE})"
  RESULT=1
fi

exit $RESULT
