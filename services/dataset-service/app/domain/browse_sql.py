"""SQL generation for the pushed-down dataset browse (DST-FR-050).

Builds the WHERE / ORDER BY that DuckDB runs directly over the snapshot parquet,
so filtering + sorting + counting + paging happen in the engine (out-of-core,
global, exact counts) instead of materializing a working-set into pandas. The
clauses mirror the previous pandas semantics exactly:

- a filter on a NUMERIC column with a numeric op (gt/gte/lt/lte/eq/neq) and a
  value that parses as a float → a typed numeric comparison (nulls excluded);
- otherwise a string comparison on ``lower(cast(col as varchar))``: eq / neq are
  case-insensitive equality; every other op (contains, and gt/gte/lt/lte on a
  non-numeric column — matching the old fall-through) is a case-insensitive
  substring LIKE;
- a filter naming an unknown column, or with an empty/None value, is skipped.

Values are always bound parameters (never interpolated); identifiers are the
dataset's own column names, validated against the real schema and double-quoted.
"""

from __future__ import annotations

from typing import Any

_NUMERIC_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=", "neq": "!="}


def is_numeric_type(duckdb_type: str) -> bool:
    """True for DuckDB numeric column types (matches pandas is_numeric_dtype for
    the types parquet round-trips: ints, floats, decimals)."""
    t = (duckdb_type or "").upper()
    return any(k in t for k in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL", "NUMERIC"))


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _escape_like(s: str) -> str:
    # escape the LIKE metacharacters (and the escape char itself) so a filter
    # value is matched literally, matching pandas str.contains(regex=False).
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_where(filters: list[dict] | None, col_numeric: dict[str, bool]) -> tuple[str, list[Any]]:
    """Return (where_sql, params). ``col_numeric`` maps real column name → is-numeric."""
    clauses: list[str] = []
    params: list[Any] = []
    for f in filters or []:
        col = f.get("col")
        op = f.get("op") or "eq"
        val = f.get("value")
        if col not in col_numeric or val is None or val == "":
            continue
        qcol = _qident(col)
        if op in _NUMERIC_OPS and col_numeric[col]:
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            clauses.append(f"{qcol} {_NUMERIC_OPS[op]} ?")
            params.append(num)
        else:
            expr = f"lower(cast({qcol} as varchar))"
            if op == "eq":
                clauses.append(f"{expr} = lower(?)")
                params.append(str(val))
            elif op == "neq":
                clauses.append(f"{expr} <> lower(?)")
                params.append(str(val))
            else:  # contains / gt/gte/lt/lte on a non-numeric column
                clauses.append(f"{expr} LIKE '%' || lower(?) || '%' ESCAPE '\\'")
                params.append(_escape_like(str(val)))
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


def build_order(sort_col: str | None, sort_dir: str, columns: set[str]) -> str:
    """ORDER BY clause. A valid sort column sorts NULLS LAST with the file-order
    ordinal (``__ord``) as a stable tiebreak (matching pandas' stable mergesort);
    no/invalid sort falls back to pure file order."""
    if sort_col and sort_col in columns:
        direction = "DESC" if sort_dir == "desc" else "ASC"
        return f"ORDER BY {_qident(sort_col)} {direction} NULLS LAST, __ord ASC"
    return "ORDER BY __ord ASC"
