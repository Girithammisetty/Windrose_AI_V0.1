#!/usr/bin/env bash
# Volume/load soak — WS5 (BRD 58): "add a volume load test at 1M rows for
# WS4 items". Proves the two WS4 fixes that were specifically about unbounded
# memory/row-count (B1 streaming Iceberg commit, B5 bulk case-service reindex)
# actually hold at real scale, not just at the small fixture sizes their own
# unit/integration tests use.
#
# Each test seeds real rows (case-service: pgx COPY straight into Postgres,
# bypassing business rules deliberately — this is fixture seeding for the READ
# path, not a re-check of case-creation rules; py-common: a real staged
# parquet file through the real stage()/commit() path) and asserts the
# operation completes and, for the Iceberg leg, that peak memory during
# commit() stays bounded (tracemalloc) rather than growing with row count.
#
# Needs: the dev Postgres reachable for case-service (testcontainers spins its
# own fresh one; the local Postgres is unused by this leg) and the real
# Iceberg REST + MinIO reachable for the py-common leg (same infra B1's tests
# already require). No live application services are booted — both tests run
# through their existing test suites (go test / pytest), not against a
# deployed stack.
#
# Usage: make soak-volume
#   VOLUME_ROWS=1000000 make soak-volume   # the BRD's literal 1M-row scale
#                                           # (default: 100000, ~10s total)
set -uo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

R=$'\033[31m'; G=$'\033[32m'; B=$'\033[1m'; N=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
die(){ printf '%sSOAK-VOLUME FAIL%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

ROWS="${VOLUME_ROWS:-100000}"

step "1/2  B5 volume: case-service full-tenant reindex at ${ROWS} rows"
(
  cd "$ROOT/services/case-service" &&
  CASE_IT=1 CASE_VOLUME_ROWS="$ROWS" go test ./test/integration/... -run TestVolumeReindexAtScale -v -timeout 900s
) || die "case-service reindex did not complete cleanly at ${ROWS} rows"

step "2/2  B1 volume: Iceberg commit at ${ROWS} rows stays memory-bounded"
(
  cd "$ROOT/libs/py-common" &&
  ICEBERG_VOLUME_ROWS="$ROWS" .venv/bin/python -m pytest tests/test_iceberg.py::test_commit_streams_large_volume_in_bounded_memory -v -s
) || die "Iceberg commit did not stay bounded at ${ROWS} rows"

printf '\n%sSOAK-VOLUME PASS%s — B1 + B5 hold at %s rows.\n' "$G" "$N" "$ROWS"
