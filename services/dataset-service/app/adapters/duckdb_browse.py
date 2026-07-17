"""DuckDB-backed browse execution (DST-FR-050).

Runs the paginated browse (filter + sort + count + page) directly over a
snapshot's parquet files, so nothing larger than the returned page is
materialized and sort/filter/counts are GLOBAL + exact regardless of table size
(replacing the old load-a-200k-working-set-into-pandas path). Used by both
catalog adapters: LocalCatalog passes a local file path; IcebergRestCatalog
passes the snapshot's ``s3://`` data files (+ an s3 config so httpfs can read
them).

A stable file-order ordinal (``__ord``) reproduces the previous pandas ordering:
row/insertion order by default, and a stable tiebreak under an explicit sort.
"""

from __future__ import annotations

from typing import Any

import duckdb

from app.domain.browse_sql import (
    _qident,
    build_order,
    build_where,
    is_numeric_type,
)


def _q(path: str) -> str:
    return "'" + path.replace("'", "''") + "'"


def _relation(uris: list[str]) -> str:
    """A read_parquet over the data files WITHOUT helper columns (for DESCRIBE)."""
    if len(uris) == 1:
        return f"read_parquet({_q(uris[0])})"
    return "read_parquet([" + ", ".join(_q(u) for u in uris) + "])"


def _base(uris: list[str]) -> str:
    """The scan relation with a stable ``__ord`` ordinal. One file → the parquet
    row number; many files → a global row_number over (filename, row number) so
    paging is deterministic across the snapshot's files."""
    if len(uris) == 1:
        return (
            f"(SELECT * EXCLUDE (file_row_number), file_row_number AS __ord "
            f"FROM read_parquet({_q(uris[0])}, file_row_number=true))"
        )
    arr = "[" + ", ".join(_q(u) for u in uris) + "]"
    return (
        "(SELECT * EXCLUDE (filename, file_row_number), "
        "row_number() OVER (ORDER BY filename, file_row_number) AS __ord "
        f"FROM read_parquet({arr}, filename=true, file_row_number=true))"
    )


def _configure_s3(con: duckdb.DuckDBPyConnection, s3: dict[str, Any]) -> None:
    con.execute("INSTALL httpfs; LOAD httpfs;")
    endpoint = (s3.get("endpoint") or "").replace("http://", "").replace("https://", "")
    use_ssl = "true" if str(s3.get("endpoint", "")).startswith("https://") else "false"
    con.execute(f"SET s3_region='{s3.get('region', 'us-east-1')}';")
    if endpoint:
        con.execute(f"SET s3_endpoint='{endpoint}';")
    con.execute(f"SET s3_access_key_id='{s3.get('access_key', '')}';")
    con.execute(f"SET s3_secret_access_key='{s3.get('secret_key', '')}';")
    con.execute(f"SET s3_use_ssl={use_ssl};")
    con.execute("SET s3_url_style='path';")


def browse_parquet(
    *,
    source_uris: list[str],
    filters: list[dict] | None,
    sort_col: str | None,
    sort_dir: str,
    offset: int,
    limit: int,
    s3: dict[str, Any] | None = None,
) -> tuple[list[str], list[list[Any]], int, int]:
    """Return (columns, page_rows (native values), total, filtered)."""
    con = duckdb.connect()
    try:
        if s3:
            _configure_s3(con, s3)
        rel = _relation(source_uris)
        desc = con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
        columns = [r[0] for r in desc]
        col_numeric = {r[0]: is_numeric_type(r[1]) for r in desc}

        where, params = build_where(filters, col_numeric)
        order = build_order(sort_col, sort_dir, set(columns))
        base = _base(source_uris)

        total = con.execute(f"SELECT count(*) FROM {base} b").fetchone()[0]
        filtered = con.execute(
            f"SELECT count(*) FROM {base} b WHERE {where}", params
        ).fetchone()[0]

        cols_sql = ", ".join(_qident(c) for c in columns)
        page = con.execute(
            f"SELECT {cols_sql} FROM {base} b WHERE {where} {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return columns, [list(r) for r in page], int(total), int(filtered)
    finally:
        con.close()
