"""Chart-config -> compile-request mapping (SEM-FR-026, BR-13).

Preserves the V1 aggregation spec (§3) semantics:
- pie_chart: single dimension + single metric, agg from `meta.aggregate.type`;
- vertical_bar_chart / vertical_stackedbar_chart: per-Y agg via `ySeries`;
- line_chart: optional `dataseries` becomes a leading dimension, ORDER BY dims;
- scatter_plot: `meta.aggregate.checked` defaults FALSE -> raw passthrough;
- sankey_chart / grid_chart: always passthrough (no aggregation rewrite).

Y entries reference either a model measure (`{"measure": "revenue"}`) or a V1
column+aggregateType pair (`{"column": "order_total", "aggregate_type": "sum"}`)
which maps onto the bootstrap naming convention `<agg>_<column>` (SEM-FR-060) —
either way the name must resolve in the published model (safety rule e).

The mapper produces a plain compile-request body handed to the SAME compiler as
POST /compile — the SEM-FR-081 byte-identity guarantee is by construction.
"""

from __future__ import annotations

from app.domain.definition import AGG_WHITELIST
from app.domain.errors import UnknownMetric, ValidationFailed

AGGREGATING_CHART_TYPES = (
    "pie_chart", "vertical_bar_chart", "vertical_stackedbar_chart",
    "line_chart", "scatter_plot",
)
PASSTHROUGH_CHART_TYPES = ("sankey_chart", "grid_chart")  # BR-13


def _to_prop_name(column: str) -> str:
    """V1 `toPropName`: snake_case -> camelCase (ySeries keys)."""
    head, *rest = column.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _y_entries(body: dict) -> list[dict]:
    y = body.get("y")
    if y is None:
        return []
    if isinstance(y, str | dict):
        y = [y]
    out = []
    for item in y:
        if isinstance(item, str):
            out.append({"column": item})
        elif isinstance(item, dict):
            out.append(item)
        else:
            raise ValidationFailed("y entries must be strings or objects")
    return out


def _metric_name(entry: dict, default_agg: str, meta: dict) -> str:
    if entry.get("measure"):
        return entry["measure"]
    column = entry.get("column")
    if not column:
        raise ValidationFailed("each y entry needs a measure or a column")
    y_series = meta.get("ySeries") or {}
    series = y_series.get(_to_prop_name(column)) or y_series.get(column) or {}
    agg = (
        entry.get("aggregate_type")
        or entry.get("aggregateType")
        or series.get("aggregateType")
        or default_agg
    )
    if agg not in AGG_WHITELIST:  # AC-4 also guards the chart path
        raise UnknownMetric(
            f"aggregate type {agg!r} not allowed; allowed: {', '.join(AGG_WHITELIST)}")
    return f"{agg}_{column}"


def map_chart_request(body: dict) -> dict:
    """Return {"passthrough": True} or a compile-request body dict."""
    chart_type = body.get("chart_type")
    if chart_type in PASSTHROUGH_CHART_TYPES:
        return {"passthrough": True, "reason": f"chart_type {chart_type} is passthrough"}
    if chart_type not in AGGREGATING_CHART_TYPES:
        raise ValidationFailed(
            f"unknown chart_type {chart_type!r}; aggregating: "
            f"{', '.join(AGGREGATING_CHART_TYPES)}; passthrough: "
            f"{', '.join(PASSTHROUGH_CHART_TYPES)}")

    meta = body.get("meta") or {}
    aggregate = meta.get("aggregate") or {}
    # V1 gate: scatter defaults to raw points; others default to aggregated
    default_checked = chart_type != "scatter_plot"
    checked = aggregate.get("checked", default_checked)
    if not checked:
        return {"passthrough": True,
                "reason": "meta.aggregate.checked is false (raw rows)"}

    x = body.get("x")
    if not x:
        raise ValidationFailed(f"chart_type {chart_type} requires config.x")
    y_entries = _y_entries(body)
    if not y_entries:
        raise ValidationFailed(f"chart_type {chart_type} requires config.y")

    default_agg = aggregate.get("type", "sum") if chart_type == "pie_chart" else "sum"
    if chart_type == "pie_chart":
        if len(y_entries) > 1:
            raise ValidationFailed("pie_chart takes a single y metric")
        if default_agg not in AGG_WHITELIST:
            raise UnknownMetric(
                f"aggregate type {default_agg!r} not allowed; allowed: "
                f"{', '.join(AGG_WHITELIST)}")

    metrics = []
    for entry in y_entries:
        name = _metric_name(entry, default_agg, meta)
        if name not in metrics:
            metrics.append(name)

    dimensions: list[dict] = []
    dataseries = body.get("dataseries")
    if dataseries and chart_type in ("line_chart", "scatter_plot"):
        dimensions.append({"name": dataseries, "grain": None})
    dimensions.append({"name": x, "grain": body.get("x_grain")})

    request: dict = {
        "metrics": metrics,
        "dimensions": dimensions,
        "filters": body.get("filters") or [],
        "time_range": body.get("time_range"),
        "limit": body.get("limit"),
        "join_paths": body.get("join_paths") or [],
    }
    if chart_type == "line_chart":
        # V1 spec §3.3: rendering order matters -> ORDER BY series, dimension
        request["order_by"] = [{"name": d["name"], "desc": False} for d in dimensions]
    return request
