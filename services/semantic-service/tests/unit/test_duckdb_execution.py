"""Compiled duckdb-dialect SQL EXECUTES against an in-process DuckDB with
synthetic data, and the aggregation results are correct (task mandate; also
covers AC-1/AC-2 end-to-end at the engine boundary)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import pytest

from app.compiler.compiler import Compiler, normalize_request
from app.domain.definition import parse_definition
from tests.conftest import SALES_DEFINITION, make_settings

SETTINGS = make_settings()
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

ORDERS = [
    # order_id, customer_id, region, order_date, order_total, discount, status, gmv
    (1, 10, "EMEA", date(2026, 1, 15), 100.0, 5.0, "completed", 100.0),
    (2, 10, "EMEA", date(2026, 2, 10), 50.0, 0.0, "pending", 50.0),
    (3, 11, "AMER", date(2026, 2, 20), 200.0, 10.0, "completed", 200.0),
    (4, 12, "AMER", date(2026, 3, 5), 25.0, 0.0, "cancelled", 25.0),
    (5, 12, "APAC", date(2025, 6, 1), 75.0, 2.5, "completed", 75.0),
]
CUSTOMERS = [(10, "gold", "Acme", date(2024, 1, 1)),
             (11, "silver", "Globex", date(2024, 6, 1)),
             (12, "gold", "Initech", date(2025, 2, 1))]


@pytest.fixture
def db():
    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS bronze")
    conn.execute("CREATE SCHEMA bronze.t42")
    conn.execute(
        "CREATE TABLE bronze.t42.ds_orders (order_id BIGINT, customer_id BIGINT, "
        "region VARCHAR, order_date DATE, order_total DOUBLE, discount DOUBLE, "
        "status VARCHAR, gmv_amount DOUBLE)")
    conn.executemany(
        "INSERT INTO bronze.t42.ds_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ORDERS)
    conn.execute(
        "CREATE TABLE bronze.t42.ds_customers (id BIGINT, tier VARCHAR, "
        "name VARCHAR, signup_date DATE)")
    conn.executemany("INSERT INTO bronze.t42.ds_customers VALUES (?, ?, ?, ?)",
                     CUSTOMERS)
    yield conn
    conn.close()


def compile_duckdb(body: dict):
    compiler = Compiler(parse_definition(SALES_DEFINITION, settings=SETTINGS),
                        model_version_label="sales@v1", settings=SETTINGS,
                        now=NOW, timezone="UTC")
    return compiler.compile(normalize_request(body, SETTINGS), "duckdb")


## The compiler emits FROM/JOIN as a `{{dataset('name')}}` macro (QRY-FR-005),
# never the literal physical table — that's what lets query-service's
# tenant-namespace guard allow it (BR-2: the guard only trusts identifiers it
# resolves itself). In production query-service text-substitutes the macro for
# the real quoted identifier before execution (sqlsafe.Rewrite); here we do the
# same tiny substitution locally so this suite can still run the compiled SQL
# straight against an in-process DuckDB and assert real aggregation results.
_MACRO_TO_TABLE = {
    "{{dataset('ds_orders')}}": '"bronze"."t42"."ds_orders"',
    "{{dataset('ds_customers')}}": '"bronze"."t42"."ds_customers"',
}


def _resolve_dataset_macros(sql: str) -> str:
    for macro, table in _MACRO_TO_TABLE.items():
        sql = sql.replace(macro, table)
    return sql


def run(db, compiled):
    sql = _resolve_dataset_macros(compiled.sql)
    return db.execute(sql, [p["value"] for p in compiled.params]).fetchall()


def test_sum_by_region_executes_with_correct_buckets(db):
    compiled = compile_duckdb({"metrics": ["revenue"], "dimensions": ["region"]})
    rows = dict(run(db, compiled))
    assert rows == {"EMEA": 150.0, "AMER": 225.0, "APAC": 75.0}


def test_filters_bind_and_aggregate_correctly(db):
    compiled = compile_duckdb({
        "metrics": ["revenue", "order_count"], "dimensions": ["region"],
        "filters": [{"dimension": "status", "op": "=", "values": ["completed"]}],
    })
    rows = {r[0]: (r[1], r[2]) for r in run(db, compiled)}
    assert rows == {"EMEA": (100.0, 1), "AMER": (200.0, 1), "APAC": (75.0, 1)}


def test_time_grain_and_relative_range(db):
    compiled = compile_duckdb({
        "metrics": ["revenue"],
        "dimensions": [{"name": "order_month", "grain": "month"}],
        "time_range": {"dimension": "order_date", "relative": "last_12_months"},
    })
    rows = {r[0].strftime("%Y-%m"): r[1] for r in run(db, compiled)}
    # 2025-06 order is outside last_12_months (2025-07-01 .. 2026-07-01)
    assert rows == {"2026-01": 100.0, "2026-02": 250.0, "2026-03": 25.0}


def test_count_distinct_and_avg(db):
    compiled = compile_duckdb({"metrics": ["region_count", "avg_order_value"]})
    [(distinct_regions, avg_value)] = run(db, compiled)
    assert distinct_regions == 3
    assert avg_value == pytest.approx(90.0)


def test_first_is_deterministic_by_primary_key(db):
    """BR-8: first orders by the entity primary key (order_id) — arg_min."""
    compiled = compile_duckdb({"metrics": ["first_status"], "dimensions": ["region"]})
    rows = dict(run(db, compiled))
    assert rows == {"EMEA": "completed", "AMER": "completed", "APAC": "completed"}
    compiled2 = compile_duckdb({"metrics": ["first_status"],
                                "dimensions": ["region"],
                                "order_within_group": "order_date"})
    assert dict(run(db, compiled2))["AMER"] == "completed"


def test_measure_level_filter(db):
    compiled = compile_duckdb({"metrics": ["completed_revenue"],
                               "dimensions": ["region"]})
    rows = dict(run(db, compiled))
    assert rows == {"EMEA": 100.0, "AMER": 200.0, "APAC": 75.0}


def test_derived_measure_safe_division(db):
    compiled = compile_duckdb({"metrics": ["aov"], "dimensions": ["region"]})
    rows = dict(run(db, compiled))
    assert rows["EMEA"] == pytest.approx(75.0)  # 150 / 2


def test_join_path_dimension(db):
    """AC-9: declared LEFT JOIN with quoted on-clause columns, executed."""
    compiled = compile_duckdb({"metrics": ["revenue"], "dimensions": ["customer_tier"]})
    assert "LEFT JOIN {{dataset('ds_customers')}} \"c\" ON \"o\".\"customer_id\" = \"c\".\"id\"" \
        in compiled.sql
    rows = dict(run(db, compiled))
    assert rows == {"gold": 250.0, "silver": 200.0}


def test_multi_entity_cte_executes(db):
    compiled = compile_duckdb({"metrics": ["revenue", "headcount"],
                               "dimensions": ["customer_tier"]})
    rows = {r[0]: (r[1], r[2]) for r in run(db, compiled)}
    assert rows == {"gold": (250.0, 2), "silver": (200.0, 1)}


def test_having_and_order_and_limit(db):
    compiled = compile_duckdb({
        "metrics": ["revenue"], "dimensions": ["region"],
        "having": [{"metric": "revenue", "op": ">", "value": 100}],
        "order_by": [{"name": "revenue", "desc": True}], "limit": 1,
    })
    assert run(db, compiled) == [("AMER", 225.0)]


def test_ac2_injection_attempt_is_inert_end_to_end(db):
    evil = "'; DROP TABLE bronze.t42.ds_orders; --"
    compiled = compile_duckdb({
        "metrics": ["revenue"], "dimensions": ["region"],
        "filters": [{"dimension": "region", "op": "IN",
                     "values": ["EMEA", evil]}],
    })
    rows = dict(run(db, compiled))
    assert rows == {"EMEA": 150.0}  # evil value matched nothing, bound as data
    # table still exists and is intact
    assert db.execute("SELECT count(*) FROM bronze.t42.ds_orders").fetchone()[0] == 5
