"""BRD 64 — real time-series forecasting (M2, Nixtla StatsForecast) + the z-score
statistical anomaly engine (M3), replacing the LinearRegression / non-runnable
placeholders. Pure compute, no infra.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.executor import anomaly, forecasting

# ---- M2: forecasting ----

def _seasonal_rows(n=60, period=12):
    t = np.arange(n)
    y = 10 + 0.3 * t + 5 * np.sin(2 * np.pi * t / period)
    return [{"y": float(v)} for v in y]


@pytest.mark.parametrize("model", ["auto_arima", "auto_ets", "auto_theta"])
def test_forecast_produces_real_horizon_and_metrics(model):
    out = forecasting.run_forecast(
        _seasonal_rows(), {"value_column": "y", "model": model,
                           "season_length": 12, "horizon": 6})
    assert out["model"] == model
    assert len(out["forecast"]) == 6
    assert all("yhat" in f for f in out["forecast"])
    # a real holdout backtest produced accuracy metrics.
    assert "mae" in out["metrics"] and "rmse" in out["metrics"]
    assert out["metrics"]["mae"] >= 0.0


def test_forecast_prediction_intervals():
    out = forecasting.run_forecast(
        _seasonal_rows(), {"value_column": "y", "model": "auto_ets",
                           "season_length": 12, "horizon": 4, "level": 90})
    assert all("lo" in f and "hi" in f for f in out["forecast"])
    assert all(f["lo"] <= f["yhat"] <= f["hi"] for f in out["forecast"])


def test_forecast_fails_closed_on_short_or_bad_series():
    with pytest.raises(ValueError):
        forecasting.run_forecast([{"y": 1.0}, {"y": 2.0}], {"value_column": "y",
                                                            "season_length": 12})
    with pytest.raises(ValueError):
        forecasting.run_forecast(_seasonal_rows(), {"value_column": "missing"})
    with pytest.raises(ValueError):
        forecasting.run_forecast(_seasonal_rows(), {"value_column": "y", "model": "prophet"})


# ---- M3: z-score statistical anomaly engine ----

def _grouped_rows():
    rows = []
    # groups g0..g4 are normal (~10); g_out is a stark outlier (~100).
    for g in range(5):
        for _ in range(20):
            rows.append({"grp": f"g{g}", "val": 10 + np.random.default_rng(g).normal(0, 0.1)})
    for _ in range(20):
        rows.append({"grp": "g_out", "val": 100.0})
    return rows


def test_anomaly_simple_value_flags_the_outlier_group():
    out = anomaly.score(_grouped_rows(),
                        {"group_column": "grp", "value_column": "val",
                         "metric": "simple_value", "threshold": 2.0})
    flagged = {g["group"] for g in out["groups"] if g["is_anomaly"]}
    assert "g_out" in flagged
    assert out["metrics"]["n_groups"] == 6
    assert out["metrics"]["anomaly_rate"] > 0


@pytest.mark.parametrize("metric", ["statistic", "entropy", "ratio", "unique", "simple_value"])
def test_anomaly_all_metrics_run(metric):
    out = anomaly.score(_grouped_rows(),
                        {"group_column": "grp", "value_column": "val", "metric": metric})
    assert len(out["groups"]) == 6
    assert all("z" in g and "is_anomaly" in g for g in out["groups"])


def test_anomaly_composite_weighted_score():
    out = anomaly.score(_grouped_rows(),
                        {"group_column": "grp", "value_column": "val", "threshold": 1.5,
                         "composite": [{"metric": "simple_value", "weight": 0.7},
                                       {"metric": "unique", "weight": 0.3}]})
    assert "g_out" in {g["group"] for g in out["groups"] if g["is_anomaly"]}


def test_anomaly_fails_closed_on_bad_params():
    with pytest.raises(ValueError):
        anomaly.score([{"a": 1}], {"group_column": "grp", "value_column": "val"})
    with pytest.raises(ValueError):
        anomaly.score(_grouped_rows(), {"group_column": "grp", "value_column": "val",
                                        "metric": "bogus"})
