"""Compiler golden tests per dialect (SEM-FR-021/022/023, BR-7).

Golden SQL files are committed under tests/unit/golden/. Regenerate with:
    GOLDEN_REGEN=1 uv run pytest tests/unit/test_compiler_golden.py
and review the diff — the golden files ARE the compiler contract.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.compiler.compiler import Compiler, normalize_request
from app.domain.definition import parse_definition
from tests.conftest import SALES_DEFINITION, make_settings

GOLDEN_DIR = Path(__file__).parent / "golden"
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

CASES: dict[str, dict] = {
    "basic_group": {
        "metrics": ["revenue"],
        "dimensions": ["region"],
    },
    "brd_example_grain_time_range": {
        "metrics": ["revenue"],
        "dimensions": [{"name": "order_month", "grain": "month"}, {"name": "region"}],
        "filters": [{"dimension": "region", "op": "IN", "values": ["EMEA", "AMER"]}],
        "time_range": {"dimension": "order_date", "relative": "last_12_months"},
        "limit": 1000,
    },
    "joined_dimension_derived_measure": {
        "metrics": ["revenue", "aov"],
        "dimensions": ["customer_tier"],
    },
    "multi_entity_cte": {
        "metrics": ["revenue", "headcount"],
        "dimensions": ["customer_tier"],
    },
    "first_and_count_distinct": {
        "metrics": ["first_status", "region_count"],
        "dimensions": ["region"],
    },
    "measure_filter_having_order": {
        "metrics": ["completed_revenue", "order_count"],
        "dimensions": ["region"],
        "filters": [{"dimension": "status", "op": "!=", "values": ["cancelled"]},
                    {"dimension": "region", "op": "LIKE", "values": ["A%"]}],
        "having": [{"metric": "order_count", "op": ">", "value": 10}],
        "order_by": [{"name": "completed_revenue", "desc": True}],
        "limit": 50,
    },
    "no_dimensions_pure_aggregate": {
        "metrics": ["revenue", "order_count"],
    },
    "between_and_null_filters": {
        "metrics": ["revenue"],
        "dimensions": ["region"],
        "filters": [
            {"dimension": "order_date", "op": "BETWEEN",
             "values": ["2026-01-01", "2026-06-30"]},
            {"dimension": "status", "op": "IS NOT NULL"},
        ],
    },
}

ALL_DIALECTS = ("duckdb", "trino", "athena", "bigquery", "synapse")
# `first` has no deterministic grouped template on synapse -> excluded (documented)
DIALECTS_FOR_CASE = {name: ALL_DIALECTS for name in CASES}
DIALECTS_FOR_CASE["first_and_count_distinct"] = ("duckdb", "trino", "athena", "bigquery")

PARAMS = [(case, dialect) for case in CASES for dialect in DIALECTS_FOR_CASE[case]]


def _compiler() -> Compiler:
    settings = make_settings()
    return Compiler(
        parse_definition(SALES_DEFINITION, settings=settings),
        model_version_label="sales@v1", settings=settings, now=NOW, timezone="UTC",
    )


def _compile(case: str, dialect: str):
    settings = make_settings()
    req = normalize_request(dict(CASES[case]), settings)
    return _compiler().compile(req, dialect)


@pytest.mark.parametrize(("case", "dialect"), PARAMS)
def test_golden_sql(case: str, dialect: str):
    compiled = _compile(case, dialect)
    golden_path = GOLDEN_DIR / f"{case}__{dialect}.sql"
    artifact = compiled.sql + "\n-- params: " + json.dumps(compiled.params) + "\n"
    if os.environ.get("GOLDEN_REGEN"):
        golden_path.write_text(artifact)
    assert golden_path.exists(), f"golden file missing: {golden_path.name}"
    assert artifact == golden_path.read_text(), f"golden drift in {golden_path.name}"


@pytest.mark.parametrize(("case", "dialect"), PARAMS)
def test_deterministic_byte_identical(case: str, dialect: str):
    """BR-7: same request + model version + dialect => identical SQL."""
    first = _compile(case, dialect)
    second = _compile(case, dialect)
    assert first.sql == second.sql
    assert first.params == second.params


def test_filters_canonicalized_before_emission():
    """BR-7: filter order in the request does not change the SQL."""
    settings = make_settings()
    a = dict(CASES["measure_filter_having_order"])
    b = dict(a)
    b["filters"] = list(reversed(a["filters"]))
    sql_a = _compiler().compile(normalize_request(a, settings), "trino").sql
    sql_b = _compiler().compile(normalize_request(b, settings), "trino").sql
    assert sql_a == sql_b


def test_group_by_uses_ordinals_and_quoted_identifiers():
    compiled = _compile("brd_example_grain_time_range", "trino")
    assert "GROUP BY 1, 2" in compiled.sql
    assert '"o"."order_date"' in compiled.sql
    assert "date_trunc('month'" in compiled.sql


def test_synapse_limit_becomes_top():
    compiled = _compile("brd_example_grain_time_range", "synapse")
    assert compiled.sql.startswith("SELECT TOP 1000 ")
    assert "LIMIT" not in compiled.sql
    assert "DATETRUNC(month, [o].[order_date])" in compiled.sql


def test_synapse_first_rejected():
    from app.domain.errors import ValidationFailed
    with pytest.raises(ValidationFailed):
        _compile("first_and_count_distinct", "synapse")


def test_output_schema_roles_and_types():
    compiled = _compile("brd_example_grain_time_range", "trino")
    assert compiled.output_schema == [
        {"name": "order_month", "type": "date", "role": "dimension"},
        {"name": "region", "type": "string", "role": "dimension"},
        {"name": "revenue", "type": "decimal", "role": "measure"},
    ]


def test_time_range_resolves_relative_bounds_ac8():
    """AC-8: relative range -> date_trunc grain + parameterized bounds +
    resolved absolute range reported."""
    compiled = _compile("brd_example_grain_time_range", "trino")
    assert compiled.time_range_resolved == {
        "dimension": "order_date", "start": "2025-07-01", "end": "2026-07-01",
        "timezone": "UTC",
    }
    assert {"type": "date", "value": "2025-07-01"} in compiled.params
    assert {"type": "date", "value": "2026-07-01"} in compiled.params
    # zero literal filter values in SQL (AC-1)
    for param in compiled.params:
        assert str(param["value"]) not in compiled.sql
