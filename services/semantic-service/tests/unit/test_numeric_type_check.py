"""BR-3 avg-of-non-numeric authoring check (definition.py) must accept the
profiler's `decimal(p,s)` type string, not just the bare `decimal` literal —
otherwise every profiled decimal column permanently fails authoring validation
with a false-positive "avg of non-numeric column" rejection."""

from __future__ import annotations

from app.domain.definition import parse_definition, validate_definition
from tests.conftest import ORDERS_URN, make_settings

SETTINGS = make_settings()

_BASE = {
    "entities": [
        {"name": "orders", "dataset_urn": ORDERS_URN, "table": "bronze.t42.ds_orders",
         "primary_key": ["order_id"], "dataset_version_policy": {"policy": "latest"}},
    ],
    "dimensions": [],
    "join_paths": [],
}


def _validate(schema: dict, agg: str, expr: str):
    doc = {**_BASE, "measures": [{"name": "m", "entity": "orders", "agg": agg, "expr": expr}]}
    defn = parse_definition(doc, settings=SETTINGS)
    lookup = lambda urn: {"exists": True, "schema": schema} if urn == ORDERS_URN else None  # noqa: E731
    return validate_definition(defn, lookup)


def test_avg_of_decimal_with_precision_scale_is_accepted():
    problems = _validate({"order_id": "long", "amount": "decimal(10,2)"}, "avg", "amount")
    assert problems == []


def test_avg_of_bare_decimal_still_accepted():
    problems = _validate({"order_id": "long", "amount": "decimal"}, "avg", "amount")
    assert problems == []


def test_avg_of_double_still_accepted():
    problems = _validate({"order_id": "long", "amount": "double"}, "avg", "amount")
    assert problems == []


def test_avg_of_genuinely_non_numeric_column_still_rejected():
    problems = _validate({"order_id": "long", "name": "string"}, "avg", "name")
    assert len(problems) == 1
    assert "avg of non-numeric column" in problems[0]["problem"]
