"""BRD 64 (M3) — the z-score STATISTICAL anomaly engine, replacing Datacern's
non-runnable `z_score_based_anomaly_detection` placeholder. Mirrors 's
z_score_based family: per-group metric components, each scored by how many standard
deviations it sits from the population mean (z-score), combined by a weighted
composite. Rule/statistics-based (no model fit) — the complement to the sklearn
IsolationForest / OneClassSVM detectors.

Pure functions over pandas: `score(rows, params) -> {scored rows + anomaly flags,
metrics}`. `metric` ∈ statistic (chi-square goodness-of-fit), entropy, ratio,
unique, simple_value. A `composite` blends several metrics by weight.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_METRICS = {"statistic", "entropy", "ratio", "unique", "simple_value"}


def _group_metric(df: pd.DataFrame, group_col: str, value_col: str, metric: str) -> pd.Series:
    """Compute one metric per group → a Series indexed by group key."""
    g = df.groupby(group_col, dropna=False)
    if metric == "simple_value":
        return g[value_col].mean()
    if metric == "unique":
        return g[value_col].nunique()
    if metric == "ratio":
        # share of each group's rows in the whole population.
        return g.size() / len(df)
    if metric == "entropy":
        def _ent(s: pd.Series) -> float:
            p = s.value_counts(normalize=True).to_numpy()
            p = p[p > 0]
            return float(-(p * np.log2(p)).sum())
        return g[value_col].apply(_ent)
    if metric == "statistic":
        # chi-square goodness-of-fit of each group's value distribution vs uniform.
        def _chi2(s: pd.Series) -> float:
            counts = s.value_counts().to_numpy(dtype=float)
            if len(counts) < 2:
                return 0.0
            expected = counts.mean()
            return float(((counts - expected) ** 2 / expected).sum())
        return g[value_col].apply(_chi2)
    raise ValueError(f"anomaly: unknown metric {metric!r}; allowed {sorted(_METRICS)}")


def _zscores(values: pd.Series) -> pd.Series:
    std = values.std(ddof=0)
    if not std or np.isnan(std):
        return pd.Series(0.0, index=values.index)
    return (values - values.mean()) / std


def score(rows: list[dict], params: dict) -> dict:
    """Score each group's anomaly by z-score of its metric(s).

    params: {group_column, value_column, metric (one of _METRICS) OR
    composite: [{metric, weight}], threshold (z, default 3.0)}. Returns
    {groups: [{group, score, z, is_anomaly}], metrics: {anomaly_rate, n_groups}}.
    """
    group_col = params.get("group_column") or params.get("group_by")
    value_col = params.get("value_column") or params.get("value")
    if not group_col or not value_col:
        raise ValueError("anomaly: group_column and value_column are required")
    df = pd.DataFrame(rows)
    for c in (group_col, value_col):
        if c not in df.columns:
            raise ValueError(f"anomaly: column {c!r} not in {list(df.columns)}")
    threshold = float(params.get("threshold", 3.0))

    composite = params.get("composite")
    if composite:
        total_w = sum(float(c.get("weight", 1.0)) for c in composite) or 1.0
        combined: pd.Series | None = None
        for spec in composite:
            m = str(spec.get("metric"))
            if m not in _METRICS:
                raise ValueError(f"anomaly: unknown metric {m!r}")
            w = float(spec.get("weight", 1.0)) / total_w
            z = _zscores(_group_metric(df, group_col, value_col, m)).abs() * w
            combined = z if combined is None else combined.add(z, fill_value=0.0)
        z_series = combined
    else:
        metric = str(params.get("metric", "simple_value"))
        if metric not in _METRICS:
            raise ValueError(f"anomaly: unknown metric {metric!r}; allowed {sorted(_METRICS)}")
        z_series = _zscores(_group_metric(df, group_col, value_col, metric)).abs()

    groups = []
    for key, z in z_series.items():
        zf = float(z) if not np.isnan(z) else 0.0
        groups.append({"group": key if not isinstance(key, np.generic) else key.item(),
                       "z": zf, "is_anomaly": bool(zf >= threshold)})
    n_anom = sum(1 for g in groups if g["is_anomaly"])
    return {
        "groups": groups,
        "metrics": {
            "anomaly_rate": float(n_anom / len(groups)) if groups else 0.0,
            "n_groups": float(len(groups)),
            "n_anomalies": float(n_anom),
        },
    }
