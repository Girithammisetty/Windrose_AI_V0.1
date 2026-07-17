"""Per-scorer aggregation over case results (EVL-FR-050).

Given raw case results, compute the aggregates gate rules reference:
``mean`` (weighted mean of scores), ``pass_rate`` (weighted fraction passed),
``count``. Weights come from per-case ``weight`` (EVL-FR-002)."""

from __future__ import annotations

from collections import defaultdict


def aggregate_by_scorer(results: list[dict]) -> dict[str, dict[str, float]]:
    """results: [{scorer_key, score, passed, weight}]. Returns
    {scorer_key: {mean, pass_rate, count, sum_weight}}."""
    acc: dict[str, dict[str, float]] = defaultdict(
        lambda: {"wsum": 0.0, "wscore": 0.0, "wpass": 0.0, "count": 0.0}
    )
    for r in results:
        w = float(r.get("weight", 1.0))
        a = acc[r["scorer_key"]]
        a["wsum"] += w
        a["wscore"] += w * float(r["score"])
        a["wpass"] += w * (1.0 if r["passed"] else 0.0)
        a["count"] += 1
    out: dict[str, dict[str, float]] = {}
    for key, a in acc.items():
        wsum = a["wsum"] or 1.0
        out[key] = {
            "mean": a["wscore"] / wsum,
            "pass_rate": a["wpass"] / wsum,
            "count": a["count"],
            "sum_weight": a["wsum"],
        }
    return out
