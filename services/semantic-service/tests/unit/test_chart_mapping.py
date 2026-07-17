"""Chart-config -> compile mapping matrix (SEM-FR-026, BR-13, AC-13),
including V1 edge shapes (y as string, ySeries camelCase keys, checked gates)."""

from __future__ import annotations

import pytest

from app.compiler.chart import map_chart_request
from app.domain.errors import ValidationFailed


def test_pie_chart_maps_single_dim_single_metric_with_meta_aggregate_type():
    """AC-13 second half: pie with meta.aggregate.type avg -> avg measure."""
    mapped = map_chart_request({
        "chart_type": "pie_chart", "x": "region", "y": "order_total",
        "meta": {"aggregate": {"type": "avg", "checked": True}},
    })
    assert mapped["metrics"] == ["avg_order_total"]
    assert mapped["dimensions"] == [{"name": "region", "grain": None}]


def test_pie_chart_defaults_to_sum():
    mapped = map_chart_request({
        "chart_type": "pie_chart", "x": "region", "y": "order_total",
        "meta": {"aggregate": {"checked": True}},
    })
    assert mapped["metrics"] == ["sum_order_total"]


def test_pie_chart_rejects_multiple_y():
    with pytest.raises(ValidationFailed):
        map_chart_request({"chart_type": "pie_chart", "x": "region",
                           "y": ["a", "b"]})


def test_bar_chart_per_y_agg_via_yseries_camelcase_keys():
    """V1 spec §2.2: ySeries keys are toPropName(column) camelCase."""
    mapped = map_chart_request({
        "chart_type": "vertical_bar_chart", "x": "region",
        "y": ["order_total", "discount"],
        "meta": {"ySeries": {"orderTotal": {"aggregateType": "avg"},
                             "discount": {"aggregateType": "max"}}},
    })
    assert mapped["metrics"] == ["avg_order_total", "max_discount"]


def test_bar_chart_measure_references_pass_through():
    mapped = map_chart_request({
        "chart_type": "vertical_bar_chart", "x": "region",
        "y": [{"measure": "revenue"}, {"measure": "avg_order_value"}],
    })
    assert mapped["metrics"] == ["revenue", "avg_order_value"]
    assert "order_by" not in mapped


def test_stacked_bar_same_as_bar():
    mapped = map_chart_request({
        "chart_type": "vertical_stackedbar_chart", "x": "region",
        "y": [{"column": "order_total"}],
    })
    assert mapped["metrics"] == ["sum_order_total"]


def test_line_chart_dataseries_becomes_leading_dimension_with_order_by():
    """V1 spec §3.3: GROUP BY series, x + ORDER BY series, x."""
    mapped = map_chart_request({
        "chart_type": "line_chart", "x": "order_month", "dataseries": "region",
        "y": [{"measure": "revenue"}],
    })
    assert [d["name"] for d in mapped["dimensions"]] == ["region", "order_month"]
    assert mapped["order_by"] == [{"name": "region", "desc": False},
                                  {"name": "order_month", "desc": False}]


def test_line_chart_without_dataseries():
    mapped = map_chart_request({
        "chart_type": "line_chart", "x": "order_month", "y": [{"measure": "revenue"}],
    })
    assert [d["name"] for d in mapped["dimensions"]] == ["order_month"]
    assert mapped["order_by"] == [{"name": "order_month", "desc": False}]


def test_scatter_raw_mode_is_passthrough_by_default():
    """V1 spec §3.4: meta.aggregate.checked defaults FALSE for scatter."""
    mapped = map_chart_request({
        "chart_type": "scatter_plot", "x": "order_total", "y": ["discount"],
    })
    assert mapped["passthrough"] is True


def test_scatter_aggregated_mode_behaves_like_bar():
    mapped = map_chart_request({
        "chart_type": "scatter_plot", "x": "region", "dataseries": "status",
        "y": [{"column": "order_total", "aggregate_type": "avg"}],
        "meta": {"aggregate": {"checked": True}},
    })
    assert mapped["metrics"] == ["avg_order_total"]
    assert [d["name"] for d in mapped["dimensions"]] == ["status", "region"]


def test_sankey_is_always_passthrough_ac13():
    mapped = map_chart_request({"chart_type": "sankey_chart",
                                "dataseries": ["from", "to", "value"]})
    assert mapped["passthrough"] is True


def test_grid_is_passthrough():
    assert map_chart_request({"chart_type": "grid_chart"})["passthrough"] is True


def test_aggregate_checked_false_is_passthrough_for_aggregating_types():
    mapped = map_chart_request({
        "chart_type": "vertical_bar_chart", "x": "region", "y": ["order_total"],
        "meta": {"aggregate": {"checked": False}},
    })
    assert mapped["passthrough"] is True


def test_unknown_chart_type_rejected():
    with pytest.raises(ValidationFailed):
        map_chart_request({"chart_type": "hologram_chart", "x": "a", "y": ["b"]})


def test_missing_x_rejected():
    with pytest.raises(ValidationFailed):
        map_chart_request({"chart_type": "pie_chart", "y": "order_total"})


def test_missing_y_rejected():
    with pytest.raises(ValidationFailed):
        map_chart_request({"chart_type": "line_chart", "x": "order_month"})


def test_duplicate_y_metrics_dedup():
    mapped = map_chart_request({
        "chart_type": "vertical_bar_chart", "x": "region",
        "y": [{"column": "order_total"}, {"column": "order_total"}],
    })
    assert mapped["metrics"] == ["sum_order_total"]
