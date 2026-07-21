"""Case-search reconcile — rebuild the OpenSearch case projection for EVERY
tenant from the Postgres source of truth.

Cases live in Postgres (durable); the Cases page reads a derived OpenSearch
projection (cases-<tenant> index). The search-index consumer only projects NEW
case.events.v1 messages, so if the index is lost — e.g. the OpenSearch container
is recreated — historical cases are NOT re-projected and the page fails with
"search projection unavailable". This is the automated, all-tenant form of the
operator's ``POST /api/v1/admin/reindex``: it enumerates active tenants and asks
case-service to rebuild each index from Postgres and swap the alias. Idempotent
and safe to run any time (wired into up.sh so a stack restart self-heals).

Companion to reconcile.py (rbac projection); same pattern, different projection.

Usage:  reconcile_cases.sh            # all active tenants
        reconcile_cases.sh <tenant>   # a single tenant id
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "e2e"))

import psycopg  # noqa: E402
import requests  # noqa: E402

from lib.common import service_token  # noqa: E402

CASE = os.environ.get("CASE_URL", "http://localhost:8308")
IDENTITY_DSN = os.environ.get(
    "IDENTITY_DSN",
    "host=localhost port=5432 dbname=identity user=windrose password=windrose_dev")


def active_tenants() -> list[str]:
    with psycopg.connect(IDENTITY_DSN) as conn:
        rows = conn.execute(
            "SELECT id::text FROM tenants "
            "WHERE deleted_at IS NULL AND status = 'active' ORDER BY created_at"
        ).fetchall()
    return [r[0] for r in rows]


def reindex(tenant_id: str) -> tuple[bool, int, str]:
    # /admin/reindex reads the tenant FROM the token claims, so mint a token
    # scoped to this tenant (case.case.admin authorizes the operator reindex).
    tok = service_token("svc:case-reconcile", tenant_id, ["case.case.admin"])
    r = requests.post(f"{CASE}/api/v1/admin/reindex",
                      headers={"Authorization": f"Bearer {tok}"}, timeout=120)
    if r.status_code == 200:
        return True, int(r.json().get("data", {}).get("reindexed", 0)), ""
    return False, 0, f"{r.status_code} {r.text[:120]}"


def main() -> int:
    tenants = [sys.argv[1]] if len(sys.argv) > 1 else active_tenants()
    print(f"reconcile-cases: rebuilding case projection for {len(tenants)} "
          f"tenant(s) via {CASE}")
    ok = fail = total = 0
    for tid in tenants:
        good, n, err = reindex(tid)
        if good:
            ok += 1
            total += n
            print(f"  ok    {tid}  reindexed={n}")
        else:
            fail += 1
            print(f"  FAIL  {tid}  {err}")
    print(f"reconcile-cases done: {ok} ok, {fail} failed, {total} cases reindexed")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
