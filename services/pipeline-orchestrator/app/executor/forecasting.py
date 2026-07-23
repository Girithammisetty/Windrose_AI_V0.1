"""BRD 64 (M2) — REAL time-series forecasting via Nixtla StatsForecast, replacing
the LinearRegression stub the `stats_forecast` algorithm used to fall back to.
Forecasting component: AutoARIMA / AutoETS / AutoCES / AutoTheta
model families, configurable season length + horizon + frequency, prediction
intervals (confidence level), and a holdout backtest for MAE/RMSE. Pure functions —
the executor calls `run_forecast(rows, params)`; no state, no IO.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_MODELS = {"auto_arima", "auto_ets", "auto_ces", "auto_theta"}


def _build_model(name: str, season_length: int):
    from statsforecast.models import AutoARIMA, AutoCES, AutoETS, AutoTheta

    return {
        "auto_arima": lambda: AutoARIMA(season_length=season_length),
        "auto_ets": lambda: AutoETS(season_length=season_length),
        "auto_ces": lambda: AutoCES(season_length=season_length),
        "auto_theta": lambda: AutoTheta(season_length=season_length),
    }[name]()


def run_forecast(rows: list[dict], params: dict) -> dict:
    """Fit a StatsForecast model on a single series and forecast `horizon` steps.

    params: {value_column, time_column?, model (auto_arima|auto_ets|auto_ces|
    auto_theta), season_length, horizon, level (confidence int, optional)}.
    Returns {forecast: [{ds, yhat, lo?, hi?}], metrics: {mae, rmse, n_train, horizon},
    model, params}. Raises ValueError on an unusable series (fail closed).
    """
    from statsforecast import StatsForecast

    value_col = params.get("value_column") or params.get("target") or "y"
    model_name = str(params.get("model", "auto_arima")).lower()
    if model_name not in _MODELS:
        raise ValueError(f"forecast: unknown model {model_name!r}; allowed {sorted(_MODELS)}")
    season = max(1, int(params.get("season_length", 1)))
    horizon = max(1, int(params.get("horizon", 12)))
    level = params.get("level")

    df = pd.DataFrame(rows)
    if value_col not in df.columns:
        raise ValueError(f"forecast: value_column {value_col!r} not in {list(df.columns)}")
    y = pd.to_numeric(df[value_col], errors="coerce").dropna().to_numpy(dtype=float)
    if len(y) < max(3, season + 1):
        raise ValueError(f"forecast: need >= {max(3, season + 1)} points, got {len(y)}")

    sf_df = pd.DataFrame({
        "unique_id": "series",
        "ds": np.arange(len(y)),
        "y": y,
    })

    # Holdout backtest for honest accuracy: last `h` points as the test window.
    h_bt = min(horizon, max(1, len(y) // 5))
    metrics: dict = {"n_train": float(len(y)), "horizon": float(horizon)}
    if len(y) - h_bt >= max(3, season + 1):
        train = sf_df.iloc[: len(y) - h_bt]
        sf_bt = StatsForecast(models=[_build_model(model_name, season)], freq=1)
        fc_bt = sf_bt.forecast(df=train, h=h_bt)
        pred_col = [c for c in fc_bt.columns if c not in ("unique_id", "ds")][0]
        actual = y[len(y) - h_bt:]
        pred = fc_bt[pred_col].to_numpy(dtype=float)[:h_bt]
        metrics["mae"] = float(np.mean(np.abs(actual - pred)))
        metrics["rmse"] = float(np.sqrt(np.mean((actual - pred) ** 2)))

    # Final fit on the full series → forward forecast.
    sf = StatsForecast(models=[_build_model(model_name, season)], freq=1)
    kwargs = {"df": sf_df, "h": horizon}
    if level is not None:
        kwargs["level"] = [int(level)]
    fc = sf.forecast(**kwargs)
    pred_col = [c for c in fc.columns
                if c not in ("unique_id", "ds") and not c.endswith(("-lo", "-hi"))
                and "-lo-" not in c and "-hi-" not in c][0]
    out = []
    lo_col = next((c for c in fc.columns if "-lo-" in c), None)
    hi_col = next((c for c in fc.columns if "-hi-" in c), None)
    for _, r in fc.iterrows():
        rec = {"ds": int(r["ds"]), "yhat": float(r[pred_col])}
        if lo_col is not None:
            rec["lo"] = float(r[lo_col])
            rec["hi"] = float(r[hi_col])
        out.append(rec)
    return {"forecast": out, "metrics": metrics, "model": model_name,
            "params": {"season_length": season, "horizon": horizon}}
