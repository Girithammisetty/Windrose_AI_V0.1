"""B6/B7 (BRD 58): generic retention reaper.

Two unbounded-growth classes share the same fix: delete rows past an age
threshold, in small batches so a sweep never holds a long lock on a hot table.

  B6 — outbox tables (20+ across the platform): rows are drained (MarkPublished)
       but never pruned. Prune WHERE published_col IS NOT NULL AND older than
       retention (require_published=True).
  B7 — processed_events dedup tables (~8 Python services): one row per consumed
       event forever, no TTL. Prune WHERE ts_col is older than retention
       (require_published=False — there's no "published" concept, just age).

Table-shape agnostic like OutboxRelay's OutboxTableSpec: configured by name, not
hardcoded per service, so the same helper drives every owner.

IMPORTANT: outbox tables have RLS (FORCE ROW LEVEL SECURITY) with a
tenant-scoped policy, so a plain DELETE with no session context matches ZERO
rows across tenants — not an error, just silently useless (the write-path twin
of what SEC-1 guards against for reads). Each service's own outbox dispatcher
already opens this cross-tenant door with a `set_config` GUC before querying
(e.g. dataset-service/memory-service: `app.worker='true'`) — prune_table sets
the SAME GUC, fresh, inside the same transaction as each batch delete (the
setting is transaction-local, so it does not survive across commits/batches).
Pass `worker_guc`/`worker_val` matching that service's own dispatcher exactly,
or leave both unset for a table with no cross-tenant RLS gate (e.g.
processed_events, which every tenant only ever prunes its own rows from... but
in practice the reaper runs as a background task with no tenant context either,
so most processed_events owners will also need a worker GUC — check the
table's migration for its RLS policy before wiring a new owner).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeIdentifierError(ValueError):
    """Raised if a table/column name isn't a safe SQL identifier.

    Table/column names here are always service-controlled constants, never user
    input — this is a belt against a future refactor mistake, mirroring
    go-common/outbox's identOK guard."""


@dataclass(slots=True)
class RetentionSpec:
    table: str
    ts_col: str  # column to test row age against (e.g. "published_at", "created_at")
    retention: timedelta
    require_not_null: bool = False  # True for outbox: only prune PUBLISHED rows
    batch_size: int = 1000
    worker_guc: str | None = None  # e.g. "app.worker" — set before each batch delete
    worker_val: str = "true"  # e.g. "true" (dataset-service/memory-service's own GUC value)


def _validate(spec: RetentionSpec) -> None:
    if not _IDENT_RE.match(spec.table) or not _IDENT_RE.match(spec.ts_col):
        raise UnsafeIdentifierError(
            f"unsafe identifier: table={spec.table!r} ts_col={spec.ts_col!r}"
        )
    if spec.worker_guc is not None and not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", spec.worker_guc):
        raise UnsafeIdentifierError(f"unsafe worker_guc: {spec.worker_guc!r}")


def _build_delete(spec: RetentionSpec) -> sa.TextClause:
    null_guard = f"{spec.ts_col} IS NOT NULL AND " if spec.require_not_null else ""
    # NOTE: the bind param must NOT be immediately followed by a `::` cast —
    # sqlalchemy.text()'s bind regex has a negative lookahead for `:`, so
    # `:retention_seconds::text` is silently NOT treated as a bind param and
    # reaches the driver as literal text (PostgresSyntaxError). This shipped
    # broken originally because the unit tests used a fake session; caught by
    # the first live-Postgres verification (see test_retention_live.py).
    age_expr = "now() - (interval '1 second' * :retention_seconds)"
    return sa.text(
        f"WITH doomed AS ("
        f"  SELECT ctid FROM {spec.table} "
        f"  WHERE {null_guard}{spec.ts_col} < {age_expr} "
        f"  LIMIT :batch"
        f") DELETE FROM {spec.table} USING doomed WHERE {spec.table}.ctid = doomed.ctid"
    )


_SET_GUC = sa.text("SELECT set_config(:guc, :val, true)")


async def prune_table(session_factory: Any, spec: RetentionSpec) -> int:
    """Delete rows past `spec.retention` in `spec.batch_size` passes until a pass
    deletes fewer than batch_size rows. Returns the total rows deleted.

    Each batch runs in its own transaction, re-asserting `worker_guc` (if set)
    immediately before the delete — matching go-common/outbox.Pruner."""
    _validate(spec)
    stmt = _build_delete(spec)
    params = {"retention_seconds": int(spec.retention.total_seconds()), "batch": spec.batch_size}

    total = 0
    async with session_factory() as session:
        while True:
            if spec.worker_guc:
                await session.execute(_SET_GUC, {"guc": spec.worker_guc, "val": spec.worker_val})
            result = await session.execute(stmt, params)
            await session.commit()
            n = result.rowcount if result.rowcount and result.rowcount > 0 else 0
            total += n
            if n < spec.batch_size:
                return total
