"""Injection defenses (SEM-FR-022, BR-1, AC-2/AC-3): the only free text in a
compile request is filter VALUES, and those exit exclusively as bound params."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.compiler.compiler import Compiler, normalize_request
from app.domain.definition import parse_definition
from app.domain.errors import (
    LimitExceeded,
    UnknownDimension,
    UnknownMetric,
    ValidationFailed,
)
from tests.conftest import SALES_DEFINITION, make_settings

SETTINGS = make_settings()


def _compiler() -> Compiler:
    return Compiler(parse_definition(SALES_DEFINITION, settings=SETTINGS),
                    model_version_label="sales@v1", settings=SETTINGS,
                    now=datetime(2026, 7, 9, tzinfo=UTC), timezone="UTC")


def test_ac2_injection_values_stay_in_params():
    evil = '"; DROP TABLE orders--'
    req = normalize_request({
        "metrics": ["revenue"], "dimensions": ["region"],
        "filters": [{"dimension": "region", "op": "IN", "values": ["EMEA", evil]}],
    }, SETTINGS)
    compiled = _compiler().compile(req, "trino")
    assert evil not in compiled.sql
    assert "DROP" not in compiled.sql
    assert "$1" in compiled.sql and "$2" in compiled.sql
    assert {"type": "string", "value": evil} in compiled.params


def test_ac3_evil_metric_name_rejected_by_regex_gate():
    with pytest.raises(UnknownMetric) as excinfo:
        normalize_request({"metrics": ["evil(); --"]}, SETTINGS)
    assert excinfo.value.code == "UNKNOWN_METRIC"


@pytest.mark.parametrize("field_name,body,exc", [
    ("dimension", {"metrics": ["revenue"],
                   "dimensions": ['region"; DROP TABLE x--']}, UnknownDimension),
    ("filter dimension", {"metrics": ["revenue"],
                          "filters": [{"dimension": "a; --", "op": "=",
                                       "values": [1]}]}, UnknownDimension),
    ("order_by", {"metrics": ["revenue"],
                  "order_by": ["revenue; DROP"]}, ValidationFailed),
    ("having metric", {"metrics": ["revenue"],
                       "having": [{"metric": "x()", "op": ">", "value": 1}]},
     UnknownMetric),
    ("join_path", {"metrics": ["revenue"],
                   "join_paths": ["p; --"]}, ValidationFailed),
    ("time_range dimension", {"metrics": ["revenue"],
                              "time_range": {"dimension": "d'--", "relative":
                                             "this_year"}}, UnknownDimension),
])
def test_name_fields_are_regex_gated(field_name, body, exc):
    with pytest.raises(exc):
        normalize_request(body, SETTINGS)


def test_like_pattern_is_a_parameter():
    req = normalize_request({
        "metrics": ["revenue"], "dimensions": ["region"],
        "filters": [{"dimension": "region", "op": "LIKE",
                     "values": ["%' OR '1'='1"]}],
    }, SETTINGS)
    compiled = _compiler().compile(req, "duckdb")
    assert "LIKE $1" in compiled.sql
    assert "'1'='1" not in compiled.sql


def test_filter_op_whitelist_enforced():
    with pytest.raises(ValidationFailed):
        normalize_request({"metrics": ["revenue"],
                           "filters": [{"dimension": "region", "op": "REGEXP",
                                        "values": ["x"]}]}, SETTINGS)


def test_filter_values_must_be_scalars():
    with pytest.raises(ValidationFailed):
        normalize_request({"metrics": ["revenue"],
                           "filters": [{"dimension": "region", "op": "=",
                                        "values": [{"$gt": 1}]}]}, SETTINGS)


def test_unknown_metric_and_dimension_resolution():
    compiler = _compiler()
    with pytest.raises(UnknownMetric):
        compiler.compile(normalize_request({"metrics": ["nope"]}, SETTINGS), "trino")
    with pytest.raises(UnknownDimension):
        compiler.compile(normalize_request(
            {"metrics": ["revenue"], "dimensions": ["nope"]}, SETTINGS), "trino")


def test_limits_enforced():
    with pytest.raises(LimitExceeded):
        normalize_request({"metrics": ["revenue"], "limit": 50_001}, SETTINGS)
    with pytest.raises(LimitExceeded):
        normalize_request(
            {"metrics": ["revenue"],
             "dimensions": [f"d{i}" for i in range(9)]}, SETTINGS)
    with pytest.raises(LimitExceeded):
        normalize_request({"metrics": [f"m{i}" for i in range(21)]}, SETTINGS)


def test_every_identifier_is_quoted():
    req = normalize_request(
        {"metrics": ["revenue", "aov"], "dimensions": ["customer_tier"]}, SETTINGS)
    compiled = _compiler().compile(req, "trino")
    # FROM/JOIN are {{dataset(...)}} macros (QRY-FR-005), never a literal
    # physical table (see compiler.py Compiler._dataset_ref); aliases, columns
    # and on-clause columns still carry dialect quoting.
    assert "{{dataset('ds_orders')}} \"o\"" in compiled.sql
    assert "{{dataset('ds_customers')}} \"c\"" in compiled.sql
    assert '"c"."tier" AS "customer_tier"' in compiled.sql
    assert 'sum("o"."order_total")' in compiled.sql
    assert 'ON "o"."customer_id" = "c"."id"' in compiled.sql
