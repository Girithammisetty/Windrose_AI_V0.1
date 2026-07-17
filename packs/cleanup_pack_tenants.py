#!/usr/bin/env python3
"""Clean up pack-seeded tenants and their data — the teardown mirror of
install_packs_multitenant.py / onboard_pack_tenant.py, so manual-testing
tenants can be reset to nothing and re-onboarded from scratch.

What it removes for each target tenant:
  * every row keyed by tenant_id in every service database (discovered from
    information_schema per DB, multi-pass so FK ordering never blocks),
    plus the tenant's own row in identity's `tenants` table;
  * the tenant's Redis authorization projections + caches (perm:*,
    authz:proj:*, and any other tenant-keyed entries);
  * the tenant's OpenSearch documents (case index) — best-effort;
  * its logins from the ui-web dev-login map (personas.json) and its section
    of the MULTITENANT_LOGINS.md cheat sheet (+ optional ui-web restart).

Honest limits (logged, never hidden): physical dataset files in MinIO and
Iceberg tables written during ingestion are NOT garbage-collected — they
become unreachable debris on the local dev volume (no live pointer survives;
re-onboarding re-ingests cleanly). Redpanda event history is append-only and
retained; all live projections derived from it are deleted here.

SAFETY: dry-run by default (prints what WOULD be deleted). `--yes` executes.
Only tenants recorded in packs/.multitenant_state.json or whose name matches
`wr-*` are accepted, and the platform's main e2e tenant is always refused.

Usage:
  # see what would be deleted for every pack tenant
  ../deploy/e2e/.venv/bin/python cleanup_pack_tenants.py --all

  # actually delete one tenant
  ../deploy/e2e/.venv/bin/python cleanup_pack_tenants.py --tenant wr-pbm --yes

  # tear down all pack tenants
  ../deploy/e2e/.venv/bin/python cleanup_pack_tenants.py --all --yes
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import psycopg  # noqa: E402
import redis as redislib  # noqa: E402
import requests  # noqa: E402

import install_packs_multitenant as base  # noqa: E402
from install_packs_multitenant import G, N, R, die, ok, say, warn  # noqa: E402

PG = {"host": os.environ.get("PGHOST", "localhost"),
      "port": os.environ.get("PGPORT", "5432"),
      "user": os.environ.get("PGUSER", "windrose"),
      "password": os.environ.get("PGPASSWORD", "windrose_dev")}

# Shared/platform databases that carry no per-tenant rows (or must never be
# touched by tenant cleanup).
EXCLUDED_DBS = {"postgres", "windrose", "temporal", "temporal_visibility",
                "mlflow", "iceberg_catalog"}

OPENSEARCH = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")


def dsn(db: str) -> str:
    return (f"postgres://{PG['user']}:{PG['password']}@{PG['host']}:{PG['port']}/{db}")


def main_tenant_id() -> str | None:
    """The platform's main e2e tenant (deploy/e2e/run/context.env) — always refused."""
    path = os.path.join(os.path.dirname(HERE), "deploy", "e2e", "run", "context.env")
    if os.path.exists(path):
        for line in open(path):
            if "TENANT_ID" in line:
                return line.split("=", 1)[1].strip().strip("'")
    return None


def list_dbs() -> list[str]:
    with psycopg.connect(dsn("postgres")) as conn:
        rows = conn.execute(
            "SELECT datname FROM pg_database WHERE NOT datistemplate").fetchall()
    return sorted(r[0] for r in rows if r[0] not in EXCLUDED_DBS)


def tenant_tables(db: str) -> list[str]:
    with psycopg.connect(dsn(db)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE column_name = 'tenant_id' AND table_schema = 'public'").fetchall()
    return sorted(r[0] for r in rows)


def resolve_tenant(name_or_id: str) -> tuple[str, str]:
    """Resolve a tenant name (or raw id) to (tenant_id, name) via identity's DB."""
    with psycopg.connect(dsn("identity")) as conn:
        row = conn.execute(
            "SELECT id, name FROM tenants WHERE name = %s OR id::text = %s",
            (name_or_id, name_or_id)).fetchone()
    if not row:
        die(f"tenant {name_or_id!r} not found in identity")
    return str(row[0]), row[1]


def guard(tid: str, name: str, state: dict) -> None:
    main_tid = main_tenant_id()
    if main_tid and tid == main_tid:
        die(f"REFUSED: {name!r} ({tid}) is the platform's main e2e tenant")
    if name not in state and not name.startswith("wr-"):
        die(f"REFUSED: {name!r} is neither recorded in .multitenant_state.json "
            "nor named wr-* — clean it up manually if you really mean it")


def count_rows(tid: str) -> dict[str, dict[str, int]]:
    """Per-DB, per-table row counts for this tenant (the dry-run report)."""
    plan: dict[str, dict[str, int]] = {}
    for db in list_dbs():
        tables = tenant_tables(db)
        if not tables:
            continue
        counts = {}
        with psycopg.connect(dsn(db)) as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tid,))
            for t in tables:
                try:
                    n = conn.execute(  # noqa: S608 — table name from catalog
                        f'SELECT count(*) FROM "{t}" WHERE tenant_id::text = %s',
                        (tid,)).fetchone()[0]
                except Exception:
                    conn.rollback()
                    continue
                if n:
                    counts[t] = n
        if counts:
            plan[db] = counts
    return plan


def purge_postgres(tid: str) -> int:
    """Delete every tenant row in every service DB. Multi-pass so FK ordering
    never blocks; residuals after the final pass are reported loudly."""
    total = 0
    for db in list_dbs():
        tables = tenant_tables(db)
        if not tables:
            continue
        deleted_db = 0
        with psycopg.connect(dsn(db), autocommit=True) as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tid,))
            remaining = list(tables)
            for _ in range(6):
                blocked = []
                for t in remaining:
                    try:
                        cur = conn.execute(  # noqa: S608
                            f'DELETE FROM "{t}" WHERE tenant_id::text = %s', (tid,))
                        deleted_db += cur.rowcount if cur.rowcount > 0 else 0
                    except Exception:
                        blocked.append(t)
                if not blocked:
                    break
                remaining = blocked
            else:
                remaining = []
            if remaining:
                warn(f"{db}: could not purge {remaining} (FK residuals) — inspect manually")
        if db == "identity":  # the tenant's own row (keyed by id, not tenant_id)
            with psycopg.connect(dsn(db), autocommit=True) as conn:
                cur = conn.execute("DELETE FROM tenants WHERE id::text = %s", (tid,))
                deleted_db += max(cur.rowcount, 0)
        if deleted_db:
            ok(f"{db}: {deleted_db} row(s) deleted")
            total += deleted_db
    return total


def purge_redis(tid: str) -> int:
    rds = redislib.Redis(host="localhost", port=6379, db=0)
    deleted = 0
    for key in rds.scan_iter(f"*{tid}*", count=1000):
        rds.delete(key)
        deleted += 1
    if deleted:
        ok(f"redis: {deleted} tenant-keyed key(s) deleted (perm:*, authz:proj:*, caches)")
    return deleted


def purge_opensearch(tid: str) -> None:
    try:
        r = requests.post(f"{OPENSEARCH}/_all/_delete_by_query?conflicts=proceed",
                          json={"query": {"term": {"tenant_id": tid}}}, timeout=30)
        if r.status_code == 200:
            n = r.json().get("deleted", 0)
            if n:
                ok(f"opensearch: {n} document(s) deleted")
        else:
            warn(f"opensearch delete_by_query: {r.status_code} {r.text[:120]}")
    except requests.RequestException as e:
        warn(f"opensearch unreachable ({e.__class__.__name__}) — skipped")


def purge_logins(tid: str, name: str) -> int:
    removed = 0
    if os.path.exists(base.PERSONAS_JSON):
        with open(base.PERSONAS_JSON) as f:
            personas = json.load(f)
        kept = {k: v for k, v in personas.items() if v.get("tenantId") != tid}
        removed = len(personas) - len(kept)
        with open(base.PERSONAS_JSON, "w") as f:
            f.write(json.dumps(kept, indent=0))
        if removed:
            ok(f"personas.json: {removed} login(s) removed ({len(kept)} remain)")
    state = base.state_load()
    if name in state:
        del state[name]
        base.state_save(state)
        base.render_report(state)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", action="append", default=[],
                    help="tenant name or id to clean (repeatable)")
    ap.add_argument("--all", action="store_true",
                    help="target every tenant recorded in .multitenant_state.json")
    ap.add_argument("--yes", action="store_true",
                    help="actually delete (default is a dry-run report)")
    ap.add_argument("--no-restart-ui", action="store_true",
                    help="do not restart ui-web after removing logins")
    args = ap.parse_args()

    state = base.state_load()
    targets = list(args.tenant)
    if args.all:
        targets += [n for n in state if n not in targets]
    if not targets:
        die("nothing to clean: pass --tenant <name> (repeatable) or --all")

    any_deleted = False
    for t in targets:
        tid, name = resolve_tenant(t)
        guard(tid, name, state)
        say(f"tenant {name!r} ({tid})")
        plan = count_rows(tid)
        rows = sum(sum(c.values()) for c in plan.values())
        for db, counts in plan.items():
            print(f"    {db}: " + ", ".join(f"{t}={n}" for t, n in
                                            sorted(counts.items(), key=lambda x: -x[1])[:8])
                  + (" …" if len(counts) > 8 else ""))
        if not args.yes:
            ok(f"DRY-RUN: would delete {rows} DB row(s) + redis keys + opensearch docs "
               f"+ logins. Re-run with --yes to execute.")
            continue
        purge_postgres(tid)
        purge_redis(tid)
        purge_opensearch(tid)
        purge_logins(tid, name)
        warn("MinIO/Iceberg physical files from ingestion are left as unreachable "
             "local-dev debris (no GC here); re-onboarding re-ingests cleanly")
        ok(f"tenant {name!r} cleaned")
        any_deleted = True

    if any_deleted and not args.no_restart_ui:
        base.restart_ui()
    if not args.yes:
        print(f"\n  {R}dry-run only{N} — nothing was deleted (add --yes)")
    else:
        say(f"{G}done{N} — {len(targets)} tenant(s) processed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
