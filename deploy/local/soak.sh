#!/usr/bin/env bash
# Restart-survival soak — prove the platform survives an infra restart with its
# data AND derived projections intact. This is the durability contract the named
# volumes + boot reconciles are supposed to guarantee, exercised end to end:
#
#   baseline `make doctor` GREEN
#     -> restart the stateful containers (kill+reboot; volumes are PRESERVED)
#       -> wait for health
#         -> `make doctor` must STILL be GREEN
#
# If a store were ephemeral (no volume) or a projection weren't durable/rebuilt,
# the post-restart doctor goes RED and this fails — catching the exact class of
# "fragile after a restart" bug we keep hitting.
#
# Assumes the stack is already up (run `make up` first). Does NOT wipe anything.
#
# Usage: make soak      (or: deploy/local/soak.sh)
set -uo pipefail
cd "$(dirname "$0")"
LOCAL_DIR="$(pwd)"
COMPOSE="$(cd .. && pwd)/docker-compose.dev.yml"
PROJECT="windrose-dev"

R=$'\033[31m'; G=$'\033[32m'; B=$'\033[1m'; N=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
die(){ printf '%sSOAK FAIL%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

# Stateful services whose data/projections must survive a restart.
STATEFUL="postgres redis opensearch clickhouse redpanda minio iceberg-rest"

wait_healthy(){  # <compose-service> <attempts> — poll the container's healthcheck
  local c="${PROJECT}-$1-1"
  for _ in $(seq 1 "$2"); do
    local h; h="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null || echo missing)"
    case "$h" in healthy|none) return 0;; esac   # `none` = no healthcheck defined; treat running as ok
    sleep 2
  done
  return 1
}

step "1/4  Baseline health (must be GREEN before a soak means anything)"
if ! ./doctor.sh; then
  die "baseline doctor is RED — the stack isn't durable/healthy yet. Recreate infra to apply volumes: ${B}make down ARGS=--infra && make up${N}, then re-run ${B}make soak${N}."
fi

step "2/4  Restart stateful infra (the 'kill' — volumes are preserved)"
# `restart` recreates the container process but keeps the named volume, exactly
# modelling a crash/reboot (NOT `down -v`, which would wipe — that's `make reset`).
docker compose -f "$COMPOSE" restart $STATEFUL || die "docker compose restart failed"

step "3/4  Wait for infra to come back healthy"
for svc in $STATEFUL; do
  if wait_healthy "$svc" 60; then printf '  %s✓%s %s\n' "$G" "$N" "$svc"
  else die "$svc did not return to healthy within timeout"; fi
done

step "4/4  Health after restart (data + projections must have survived)"
if ./doctor.sh; then
  printf '\n%sSOAK PASS%s — platform survived an infra restart, still GREEN.\n' "$G" "$N"
  exit 0
else
  die "doctor is RED after restart — a store lost its data or a projection wasn't rebuilt (durability regression)."
fi
