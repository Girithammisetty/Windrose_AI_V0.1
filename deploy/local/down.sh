#!/usr/bin/env bash
# Windrose `make down` — stop every native service started by `make up`.
# Pass --infra to also stop the Docker infra stack.
#   make down                 # stop services, leave infra up
#   make down ARGS=--infra    # stop services + docker compose down
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"

DOWN_INFRA=0
for a in "$@"; do case "$a" in --infra) DOWN_INFRA=1;; esac; done

BLU=$'\e[36m'; GRN=$'\e[32m'; NC=$'\e[0m'
say() { echo "${BLU}==>${NC} $*"; }
ok()  { echo "${GRN}  ok${NC} $*"; }

say "stopping Windrose services"
if [ -f "$PID_DIR/all.pids" ]; then
  while read -r p; do [ -n "$p" ] && kill "$p" 2>/dev/null; done < "$PID_DIR/all.pids"
fi
pkill -f 'e2e/run/bin/' 2>/dev/null
pkill -f 'uvicorn app.main:app' 2>/dev/null
pkill -f 'http.server 8300' 2>/dev/null
pkill -f 'tsx .*bff-graphql' 2>/dev/null
pkill -f 'next dev' 2>/dev/null
pkill -f 'next-server' 2>/dev/null
for port in 3000 4000 8085 8086 8300 8301 8302 8303 8304 8305 8306 8307 8308 8310 \
            8311 8312 8313 8314 8315 8316 8320 8321 8322 8323 8324; do
  pid=$(lsof -ti tcp:$port 2>/dev/null); [ -n "$pid" ] && kill $pid 2>/dev/null
done
# SIGKILL escalation for anything that ignored SIGTERM (e.g. realtime-hub's
# graceful SSE drain) so `make down` is deterministic.
sleep 2
pkill -9 -f 'e2e/run/bin/' 2>/dev/null
pkill -9 -f 'uvicorn app.main:app' 2>/dev/null
for port in 3000 4000 8085 8086 8305 8315; do
  pid=$(lsof -ti tcp:$port 2>/dev/null); [ -n "$pid" ] && kill -9 $pid 2>/dev/null
done
: > "$PID_DIR/all.pids" 2>/dev/null || true
ok "services stopped"

if [ "$DOWN_INFRA" = 1 ]; then
  say "stopping Docker infra (docker compose down)"
  ( cd "$REPO_DIR" && docker compose -f deploy/docker-compose.dev.yml down )
  ok "infra stopped"
else
  ok "infra left running (pass ARGS=--infra to stop it too)"
fi
