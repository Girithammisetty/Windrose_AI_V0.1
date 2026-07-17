"""Per-engine SQL dialect table (SEM-FR-022b/023).

Every identifier that reaches SQL goes through `Dialect.quote`; every filter
value exits as a positional `$n` placeholder bound out-of-band (SEM-FR-022c) —
$n (not `?`) because that is the binds-mode contract query-service's
sqlsafe.ScanBinds enforces on /sql/run, and executors (chart-service) pass the
compiled params as ordered `binds`. `first` and `count_distinct` have
per-dialect templates; `first` is always deterministic (BR-8) — `arbitrary()`
is never emitted.

| dialect  | quoting          | date_trunc         | first                        |
|----------|------------------|--------------------|------------------------------|
| duckdb   | "x" ("" escape)  | date_trunc('g', x) | arg_min(x, order)            |
| trino    | "x" ("" escape)  | date_trunc('g', x) | min_by(x, order)             |
| athena   | "x" ("" escape)  | date_trunc('g', x) | min_by(x, order)             |
| bigquery | `x` (\\` escape) | date_trunc(x, g)   | array_agg(... LIMIT 1)[0]    |
| synapse  | [x] (]] escape)  | DATETRUNC(g, x)    | unsupported -> 422           |
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.errors import ValidationFailed


@dataclass(frozen=True, slots=True)
class Dialect:
    name: str
    quote_open: str
    quote_close: str
    escape: str  # replacement for a close-quote char inside an identifier
    group_by_ordinals: bool = True  # T-SQL forbids ordinals in GROUP BY

    def quote(self, identifier: str) -> str:
        return (
            self.quote_open
            + identifier.replace(self.quote_close, self.escape)
            + self.quote_close
        )

    def quote_table(self, dotted: str) -> str:
        return ".".join(self.quote(part) for part in dotted.split("."))

    def date_trunc(self, grain: str, expr: str) -> str:
        if self.name == "bigquery":
            return f"date_trunc({expr}, {grain})"
        if self.name == "synapse":
            return f"DATETRUNC({grain}, {expr})"
        return f"date_trunc('{grain}', {expr})"

    def count_distinct(self, expr: str) -> str:
        return f"count(DISTINCT {expr})"

    def first(self, expr: str, order_expr: str) -> str:
        """Deterministic `first` (BR-8): ordered by the entity primary key or
        the request's order_within_group; nondeterministic arbitrary() never."""
        if self.name in ("trino", "athena"):
            return f"min_by({expr}, {order_expr})"
        if self.name == "duckdb":
            return f"arg_min({expr}, {order_expr})"
        if self.name == "bigquery":
            return f"array_agg({expr} ORDER BY {order_expr} LIMIT 1)[OFFSET(0)]"
        raise ValidationFailed(
            f"agg 'first' is not supported on dialect {self.name!r} "
            "(no deterministic grouped-first template)")


DIALECTS: dict[str, Dialect] = {
    "duckdb": Dialect("duckdb", '"', '"', '""'),
    "trino": Dialect("trino", '"', '"', '""'),
    "athena": Dialect("athena", '"', '"', '""'),
    "bigquery": Dialect("bigquery", "`", "`", "\\`"),
    "synapse": Dialect("synapse", "[", "]", "]]", group_by_ordinals=False),
}


def get_dialect(name: str) -> Dialect:
    dialect = DIALECTS.get(name)
    if dialect is None:
        raise ValidationFailed(
            f"unknown dialect {name!r}; supported: {', '.join(sorted(DIALECTS))}")
    return dialect
