"""Fixture warehouse for sql_result_equivalence (BR-4/BR-9).

A read-only embedded DuckDB eval schema seeded per dataset version. Candidate
and expected SQL execute here (never against tenant data — netpol/mode assertion
in prod); a per-query execution ceiling and fixture-side row cap prevent a
cost-bomb candidate from hanging the run. This is a genuinely real SQL engine
(DuckDB), not a simulation."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import duckdb


class DuckDbFixtureWarehouse:
    """Fixtures live as ``<dir>/<fixture>.duckdb`` files. Queries run read-only.
    Seed helper (:meth:`seed`) builds a fixture from table dicts for tests and
    dataset-version fixture provisioning."""

    def __init__(self, base_dir: str, *, ceiling_s: float = 60.0, row_cap: int = 100_000):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ceiling_s = ceiling_s
        self._row_cap = row_cap

    def _path(self, fixture: str) -> str:
        return str(self._dir / f"{fixture}.duckdb")

    def seed(self, fixture: str, tables: dict[str, tuple[list[str], list[tuple]]]) -> None:
        """tables: {name: (columns, rows)}. Creates a fresh fixture DB with
        DuckDB-inferred column types (from a VALUES relation)."""
        path = self._path(fixture)
        if os.path.exists(path):
            os.remove(path)
        con = duckdb.connect(path)
        try:
            for name, (cols, rows) in tables.items():
                col_defs = ", ".join(f'"{c}"' for c in cols)
                if rows:
                    con.execute(
                        f'CREATE TABLE "{name}" AS '
                        f"SELECT * FROM (VALUES {_values_sql(rows, cols)}) AS t({col_defs})"
                    )
                else:
                    empty_cols = ", ".join(f'"{c}" VARCHAR' for c in cols)
                    con.execute(f'CREATE TABLE "{name}" ({empty_cols})')
        finally:
            con.close()

    def _run_sync(self, fixture: str, sql: str) -> tuple[list[str], list[tuple]]:
        path = self._path(fixture)
        con = duckdb.connect(path, read_only=os.path.exists(path))
        try:
            con.execute("SET threads TO 2")
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(self._row_cap)
            return cols, [tuple(r) for r in rows]
        finally:
            con.close()

    async def query(self, fixture: str, sql: str) -> tuple[list[str], list[tuple]]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_sync, fixture, sql), timeout=self._ceiling_s
            )
        except TimeoutError as exc:
            raise TimeoutError(f"SQL exceeded {self._ceiling_s}s execution ceiling") from exc


def _values_sql(rows: list[tuple], cols: list[str]) -> str:
    def lit(v):
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return repr(v)
        return "'" + str(v).replace("'", "''") + "'"

    return ", ".join("(" + ", ".join(lit(v) for v in row) + ")" for row in rows)
