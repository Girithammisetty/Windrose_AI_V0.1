"""Shared SQL helpers for the real query drivers.

Placeholder translation converts the service-internal named `:watermark`
placeholder (produced by ``domain.watermark.build_incremental_query``) into each
driver's native parameter style, **carrying the value out-of-band**. The value
never enters the SQL string — this is the ING-FR-061 / BR-5 guarantee that the
watermark is a bound driver parameter, not spliced text.
"""

from __future__ import annotations

import re
from typing import Any

# `:name` but not `::cast` (Postgres) and not `:=` — a plain named placeholder.
_PARAM_RE = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")


def to_positional(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """Translate `:name` placeholders to asyncpg ``$1..$n`` positional args.

    Returns ``(translated_sql, ordered_values)``. Repeated names reuse the same
    positional index. Missing params raise KeyError so a splicing bug can never
    silently fall back to text substitution.
    """
    order: dict[str, int] = {}
    values: list[Any] = []

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in order:
            values.append(params[name])
            order[name] = len(values)
        return f"${order[name]}"

    translated = _PARAM_RE.sub(repl, sql)
    return translated, values


def to_pyformat(sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate `:name` placeholders to DB-API ``%(name)s`` (aiomysql/pymysql).

    Literal ``%`` in the statement is doubled so pyformat interpolation leaves it
    intact. Only the named parameters supplied are returned.
    """
    escaped = sql.replace("%", "%%")
    used: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        used.add(name)
        return f"%({name})s"

    translated = _PARAM_RE.sub(repl, escaped)
    return translated, {k: params[k] for k in used}


def to_format(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """Translate `:name` placeholders to DB-API ``%s`` positional args.

    Used by drivers whose paramstyle is ``format`` (e.g. redshift-connector).
    Literal ``%`` is doubled so interpolation leaves it intact. Repeated names
    append the value again in positional order. The value never enters the SQL
    text (ING-FR-061, BR-5).
    """
    escaped = sql.replace("%", "%%")
    values: list[Any] = []

    def repl(match: re.Match[str]) -> str:
        values.append(params[match.group(1)])
        return "%s"

    translated = _PARAM_RE.sub(repl, escaped)
    return translated, values


def to_at_named(sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate `:name` placeholders to BigQuery/Spanner ``@name`` named params.

    Returns ``(translated_sql, used_params)``. The value never enters the SQL
    text — it is carried in the returned dict and bound as a typed query
    parameter by the driver (ING-FR-061, BR-5).
    """
    used: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        used.add(name)
        return f"@{name}"

    translated = _PARAM_RE.sub(repl, sql)
    return translated, {k: params[k] for k in used}


def wrap_top_zero(statement: str) -> str:
    """Column-introspection query that fetches no rows (SQL Server / Synapse).

    SQL Server has no ``LIMIT``; ``TOP 0`` returns the column shape only.
    """
    inner = statement.strip().rstrip(";")
    return f"SELECT TOP 0 * FROM ({inner}) _wr_cols"


def wrap_where_false(statement: str) -> str:
    """Column-introspection query that fetches no rows (Oracle).

    Oracle has neither ``LIMIT`` nor ``TOP``; ``WHERE 1=0`` returns the column
    shape only while executing no row work. The subquery alias must begin with a
    letter — Oracle rejects unquoted identifiers starting with ``_``.
    """
    inner = statement.strip().rstrip(";")
    return f"SELECT * FROM ({inner}) wr_cols WHERE 1=0"


def wrap_limit_zero(statement: str) -> str:
    """Column-introspection query that fetches no rows."""
    inner = statement.strip().rstrip(";")
    return f"SELECT * FROM ({inner}) _wr_cols LIMIT 0"


def quote_identifier(identifier: str, *, quote: str) -> str:
    """Quote a possibly dotted (schema.table / project.dataset.table)
    identifier for a dialect that quotes with the same char on both sides
    (backtick for BigQuery/Spanner/MySQL, double-quote for Postgres/Oracle/
    ANSI), escaping an embedded quote char by doubling it. Each dot-separated
    part is quoted independently so a caller-supplied table name can never
    inject a stray token via an unescaped quote or a bogus extra qualifier
    (BRD 58 SEC-5 — every ``preview(request={"table": ...})`` driver spliced
    this value into ``SELECT * FROM {table}`` unescaped)."""
    esc = quote + quote
    return ".".join(f"{quote}{part.replace(quote, esc)}{quote}" for part in identifier.split("."))


def quote_bracket_identifier(identifier: str) -> str:
    """MSSQL bracket-quoting (``[part]`` per dot-separated segment), escaping
    an embedded ``]`` by doubling it. See `quote_identifier`."""
    return ".".join(f"[{part.replace(']', ']]')}]" for part in identifier.split("."))
