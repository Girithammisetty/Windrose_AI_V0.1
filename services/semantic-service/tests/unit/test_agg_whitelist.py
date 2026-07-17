"""Aggregation-function whitelist (SEM-FR-004, SEM-FR-022a, AC-4)."""

from __future__ import annotations

import pytest

from app.compiler.chart import map_chart_request
from app.domain.definition import AGG_WHITELIST, parse_definition
from app.domain.errors import UnknownMetric, ValidationFailed
from tests.conftest import ORDERS_URN, make_settings


def _defn_with_agg(agg: str) -> dict:
    return {
        "entities": [{"name": "orders", "dataset_urn": ORDERS_URN,
                      "table": "bronze.t42.ds_orders", "primary_key": ["order_id"],
                      "dataset_version_policy": {"policy": "latest"}}],
        "measures": [{"name": "m", "entity": "orders", "agg": agg,
                      "expr": "order_total"}],
    }


def test_whitelist_is_exactly_the_brd_set():
    assert AGG_WHITELIST == ("sum", "avg", "min", "max", "count",
                             "count_distinct", "first")


@pytest.mark.parametrize("agg", AGG_WHITELIST)
def test_whitelisted_aggs_accepted_at_authoring(agg):
    parse_definition(_defn_with_agg(agg), settings=make_settings())


@pytest.mark.parametrize("agg", ["exec", "array_agg", "string_agg", "percentile",
                                 "SUM", "arbitrary", None])
def test_ac4_non_whitelisted_agg_rejected_with_allowed_list(agg):
    with pytest.raises(ValidationFailed) as excinfo:
        parse_definition(_defn_with_agg(agg), settings=make_settings())
    assert "sum, avg, min, max, count, count_distinct, first" in str(excinfo.value)


def test_chart_path_rejects_unknown_aggregate_type():
    with pytest.raises(UnknownMetric):
        map_chart_request({
            "chart_type": "vertical_bar_chart", "x": "region",
            "y": [{"column": "order_total", "aggregate_type": "exec"}],
        })


def test_unknown_agg_cannot_reach_compile():
    """SEM-FR-022a: measures are dereferenced from the model by name — a raw
    agg name in the request is treated as a metric name and regex/model gated."""
    from app.compiler.compiler import normalize_request
    with pytest.raises(UnknownMetric):
        normalize_request({"metrics": ["EXEC xp_cmdshell"]}, make_settings())
