"""Platform reconcile — self-heal the rbac permissions projection for EVERY
tenant.

The rbac permissions_flat projection lives in Redis; the Go services read it
DIRECTLY (opaclient) and deny on a miss, with no synchronous fallback. So a
Redis wipe or a cold stack restart denies every request platform-wide until the
projection is rebuilt — the recurring "everything's 403 after a restart" drift.

This is the automated, all-tenant form of the manual
``POST /api/v1/admin/projection/rebuild?tenant=<id>`` an operator ran on drift:
it enumerates active tenants from the identity registry (the source of truth)
and asks rbac-service to rebuild each; rbac marks the tenant's users dirty and
its recompute worker repopulates Redis. Idempotent and safe to run at any time
(e.g. wired into ``up.sh`` so a stack restart self-heals).

Usage:  reconcile.sh            # all active tenants
        reconcile.sh <tenant>   # a single tenant id
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "e2e"))

import psycopg  # noqa: E402
import requests  # noqa: E402

from lib.common import superadmin_token  # noqa: E402

RBAC = os.environ.get("RBAC_URL", "http://localhost:8302")
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


def rebuild(tenant_id: str, headers: dict) -> tuple[bool, int, str]:
    r = requests.post(f"{RBAC}/api/v1/admin/projection/rebuild",
                      params={"tenant": tenant_id}, headers=headers, timeout=30)
    if r.status_code in (200, 202):
        return True, int(r.json().get("users_enqueued", 0)), ""
    return False, 0, f"{r.status_code} {r.text[:120]}"


def main() -> int:
    tenants = [sys.argv[1]] if len(sys.argv) > 1 else active_tenants()
    print(f"reconcile: rebuilding rbac projection for {len(tenants)} tenant(s) "
          f"via {RBAC}")
    headers = {"Authorization": f"Bearer {superadmin_token()}"}
    ok = fail = enqueued = 0
    for tid in tenants:
        good, n, err = rebuild(tid, headers)
        if good:
            ok += 1
            enqueued += n
            print(f"  ok    {tid}  users_enqueued={n}")
        else:
            fail += 1
            print(f"  FAIL  {tid}  {err}")
    print(f"reconcile done: {ok} ok, {fail} failed, {enqueued} users enqueued "
          f"(worker repopulates Redis)")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
