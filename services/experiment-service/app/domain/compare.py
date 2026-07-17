"""Server-side run comparison matrix (EXP-FR-020, BR-9/BR-10, AC-5).

Pure function over already-fetched mirror rows: no per-run MLflow fan-out, no
per-run DB loop (the repo fetches all metrics/params for the run set in one
query each). Best-value direction is ``max`` unless the metric key matches the
loss-prefix config -> ``min``.
"""

from __future__ import annotations

from dataclasses import dataclass


def metric_direction(key: str, loss_prefixes: list[str]) -> str:
    k = key.lower()
    return "min" if any(k.startswith(p) for p in loss_prefixes) else "max"


@dataclass(slots=True)
class CompareResult:
    run_ids: list[str]
    metrics: list[dict]
    params: list[dict]
    next_cursor: str | None
    has_more: bool


def build_comparison(
    *,
    run_ids: list[str],
    metric_rows: dict[str, dict[str, float]],
    param_rows: dict[str, dict[str, str]],
    requested_metrics: list[str] | None,
    requested_params: list[str] | None,
    include_all: bool,
    loss_prefixes: list[str],
    page_size: int,
    offset: int,
) -> CompareResult:
    """``metric_rows``/``param_rows`` are {key: {run_id: value}}."""
    metric_keys = sorted(metric_rows)
    param_keys = sorted(param_rows)
    if not include_all:
        if requested_metrics:
            metric_keys = [k for k in metric_keys if k in set(requested_metrics)]
        if requested_params:
            param_keys = [k for k in param_keys if k in set(requested_params)]

    all_keys = [("metric", k) for k in metric_keys] + [("param", k) for k in param_keys]
    window = all_keys[offset : offset + page_size]
    has_more = offset + page_size < len(all_keys)

    metrics_out: list[dict] = []
    params_out: list[dict] = []
    for kind, key in window:
        if kind == "metric":
            values = metric_rows[key]
            direction = metric_direction(key, loss_prefixes)
            best_run_id = None
            if values:
                best_run_id = (
                    max(values, key=lambda r: values[r])
                    if direction == "max"
                    else min(values, key=lambda r: values[r])
                )
            metrics_out.append({
                "key": key,
                "values": {rid: values.get(rid) for rid in run_ids},
                "best_run_id": best_run_id,
                "direction": direction,
            })
        else:
            values = param_rows[key]
            distinct = {values[r] for r in values}
            params_out.append({
                "key": key,
                "values": {rid: values.get(rid) for rid in run_ids},
                "differs": len(distinct) > 1 or len(values) < len(run_ids),
            })

    next_cursor = None
    if has_more:
        from app.utils import encode_cursor

        next_cursor = encode_cursor({"o": offset + page_size})
    return CompareResult(
        run_ids=run_ids,
        metrics=metrics_out,
        params=params_out,
        next_cursor=next_cursor,
        has_more=has_more,
    )
