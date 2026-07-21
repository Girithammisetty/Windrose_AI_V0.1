#!/usr/bin/env bash
# Rebuild the OpenSearch case projection for every active tenant (or one, if an
# id is given) from the Postgres source of truth. Run after a stack restart /
# OpenSearch wipe to clear "search projection unavailable" on the Cases page, or
# let up.sh call it for you.
#
# Usage: deploy/local/reconcile_cases.sh [<tenant-uuid>]
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"

PY="$E2E/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

exec "$PY" reconcile_cases.py "$@"
