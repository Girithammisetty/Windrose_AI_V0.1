#!/usr/bin/env bash
# Self-heal the rbac permissions projection for every active tenant (or a single
# one if an id is given). Run this after a stack restart / Redis wipe to clear
# the "everything is 403 after a restart" drift, or let up.sh call it for you.
#
# Usage: deploy/local/reconcile.sh [<tenant-uuid>]
set -uo pipefail
cd "$(dirname "$0")"
E2E="$(cd ../e2e && pwd)"
source "$E2E/config.env"

PY="$E2E/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

exec "$PY" reconcile.py "$@"
