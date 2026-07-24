#!/usr/bin/env bash
# Platform health doctor — detect the "fragile after a restart" failure class
# BEFORE a user hits a broken page, and optionally heal it.
#
# Every stability incident this week had the same shape: state on a store that
# either wasn't durable (no volume) or wasn't rebuilt on boot from the Postgres
# source of truth (a Redis rbac projection / an OpenSearch case index). This
# checks both axes for every active tenant and reports GREEN/RED with a one-line
# remedy each. `--heal` runs the existing reconciles.
#
# Usage:
#   make doctor           # check only (read-only)
#   make doctor HEAL=1    # check, then rebuild any missing projections
set -uo pipefail
cd "$(dirname "$0")"
LOCAL_DIR="$(pwd)"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env" 2>/dev/null || true
PY="$E2E/.venv/bin/python"; [ -x "$PY" ] || PY="python3"
COMPOSE="$(cd .. && pwd)/docker-compose.dev.yml"
HEAL="${HEAL:-0}"; [ "${1:-}" = "--heal" ] && HEAL=1

R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
fail=0; warn=0
ok(){   printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
bad(){  printf '  %s✗%s %s\n'  "$R" "$N" "$*"; fail=$((fail+1)); }
note(){ printf '  %s!%s %s\n'  "$Y" "$N" "$*"; warn=$((warn+1)); }
hdr(){  printf '\n%s%s%s\n' "$B" "$*" "$N"; }

OS_URL="${OPENSEARCH_URL:-http://localhost:9200}"
REDIS="${REDIS_ADDR:-localhost:6379}"
IDENTITY_DSN="host=localhost port=5432 dbname=identity user=datacern password=datacern_dev"

# ---- 1. durability: every stateful store has its named volume -----------------
hdr "1. Data durability (named volumes present)"
for v in pgdata redpandadata miniodata icebergdata opensearchdata clickhousedata redisdata; do
  vol="datacern-dev_${v}"
  if docker volume inspect "$vol" >/dev/null 2>&1; then ok "$v"
  else bad "$v MISSING — not durable. Recreate infra to apply: make down ARGS=--infra && make up"; fi
done

# ---- 2. active tenants (source of truth) --------------------------------------
hdr "2. Tenant registry (Postgres = source of truth)"
TENANTS="$($PY - <<PY 2>/dev/null
import psycopg
try:
    with psycopg.connect("$IDENTITY_DSN") as c:
        for (t,) in c.execute("SELECT id::text FROM tenants WHERE deleted_at IS NULL AND status='active'").fetchall():
            print(t)
except Exception as e:
    import sys; print(f"ERR {e}", file=sys.stderr)
PY
)"
if [ -z "$TENANTS" ]; then note "no active tenants (fresh/empty platform) — nothing to reconcile"; else
  n=$(printf '%s\n' "$TENANTS" | grep -c .); ok "$n active tenant(s)"; fi

# ---- heal (optional; must run BEFORE the numbered projection checks below, so
#      HEAL=1's summary reflects the post-heal state instead of the pre-heal
#      one) -----------------------------------------------------------------
if [ "$HEAL" = 1 ] && [ -n "$TENANTS" ]; then
  hdr "Healing (rebuilding projections from Postgres)"
  ( cd "$LOCAL_DIR" && ./reconcile.sh ) 2>&1 | sed 's/^/  /' || true
  ( cd "$LOCAL_DIR" && ./reconcile_cases.sh ) 2>&1 | sed 's/^/  /' || true
fi

# ---- 3. derived projections rebuilt from the source of truth ------------------
hdr "3. Derived projections (must be rebuildable on boot, not lost)"
# 4a. Redis rbac permissions projection per tenant (the "403 after restart" class).
# Prefer the host redis-cli; fall back to the container's (host usually lacks it).
RCLI=""
if command -v redis-cli >/dev/null 2>&1 && redis-cli -u "redis://$REDIS" ping >/dev/null 2>&1; then
  RCLI="redis-cli -u redis://$REDIS"
else
  rc="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i redis | head -1)"
  [ -n "$rc" ] && docker exec "$rc" redis-cli ping >/dev/null 2>&1 && RCLI="docker exec $rc redis-cli"
fi
if [ -n "$RCLI" ]; then
  ok "redis reachable"
  for t in $TENANTS; do
    hits=$($RCLI --scan --pattern "perm:*${t}*" 2>/dev/null | head -1)
    [ -n "$hits" ] && ok "rbac projection present · $t" \
      || note "rbac projection MISSING · $t (heal: ./reconcile.sh $t; runtime SQL fallback covers reads meanwhile)"
  done
else note "redis unreachable — skipped rbac projection check"; fi
# 4b. OpenSearch case index per tenant (the "search projection unavailable" class)
if curl -sf "$OS_URL/_cluster/health" >/dev/null 2>&1; then
  ok "opensearch reachable"
  idx="$(curl -s "$OS_URL/_cat/indices/cases-*?h=index" 2>/dev/null)"
  for t in $TENANTS; do
    printf '%s' "$idx" | grep -q "cases-$t" && ok "case index present · $t" \
      || bad "case index MISSING · $t (heal: ./reconcile_cases.sh $t) — Cases page will 503"
  done
else bad "opensearch unreachable at $OS_URL — Cases search is down"; fi

# ---- summary ------------------------------------------------------------------
hdr "Summary"
if [ "$fail" -gt 0 ]; then
  printf '  %s%d problem(s)%s, %d warning(s). ' "$R" "$fail" "$N" "$warn"
  [ "$HEAL" = 1 ] || printf 'Re-run with %smake doctor HEAL=1%s to rebuild projections.' "$B" "$N"
  printf '\n'; exit 1
elif [ "$warn" -gt 0 ]; then
  printf '  %s%d warning(s)%s, no hard failures.\n' "$Y" "$warn" "$N"; exit 0
else printf '  %sall healthy.%s\n' "$G" "$N"; exit 0; fi
