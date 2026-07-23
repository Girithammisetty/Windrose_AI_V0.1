# BRD 64 — Time-series forecasting + statistical anomaly detection

**Status:** DONE — 2026-07-23 · part of the [Datacern pipeline/ML parity index](62_pipeline_ml_parity_index.md)
**Owner:** platform · **Service:** `pipeline-orchestrator` (executor) + `agent-runtime` (agent)
**Gaps closed:** M2 (real time-series forecasting), M3 (statistical z-score anomaly engine).

---

## Analysis

Two of Datacern's catalog algorithms were labeled but not real:

- **`stats_forecast` (M2)** — labeled "StatsForecast" but the local executor mapped it
  to `LinearRegression`.  ships real Nixtla StatsForecast: AutoARIMA / AutoETS
  / AutoCES / AutoTheta, season length, horizon, prediction intervals, MSTL.
- **`z_score_based_anomaly_detection` (M3)** — a `runnable=False` V1 placeholder
  (BR-14) that fell back to IsolationForest if reached.  ships a real
  statistics engine: per-group metric components (statistic / entropy / ratio /
  unique / simple_value) each z-scored against the population, combined by a weighted
  composite.

Both are pure compute (CPU, no GPU/cloud) → fully local-testable.

## Design

1. **`app/executor/forecasting.py` (new)** — `run_forecast(rows, params)` fits a real
   StatsForecast model (`auto_arima|auto_ets|auto_ces|auto_theta`) on the series,
   forecasts `horizon` steps, optionally returns prediction intervals (`level`), and
   computes honest accuracy via a **holdout backtest** (MAE/RMSE). Fails closed on a
   too-short series / missing column / unknown model.
2. **`app/executor/anomaly.py` (new)** — `score(rows, params)` computes a per-group
   metric (or a weighted `composite` of several), z-scores it against the population,
   and flags groups whose |z| ≥ threshold. Metrics: statistic (chi-square GoF),
   entropy, ratio, unique, simple_value. Fails closed on bad params.
3. **`app/executor/local.py`** — a branch in `_train_sync`: `stats_forecast` →
   `forecasting.run_forecast`, `z_score_based_anomaly_detection` → `anomaly.score`;
   each logs its real metrics + a result artifact (`forecast.json` / `anomaly.json`)
   to the real MLflow run. These aren't sklearn estimators, so no sklearn-registry
   version is minted — honest, not faked (the run + metrics + artifact are real).
4. **`app/domain/catalog.py`** — z-score anomaly is now `runnable=True` (BR-14
   retired); `lightgbm`/`statsforecast` real deps in `pyproject.toml`.
5. **Agent** — `model_training` gains forecasting + statistical-anomaly NL hints
   (`forecast`, `time series`, `arima` → `stats_forecast`; `statistical anomaly`,
   `z-score`, `outlier group` → `z_score_based_anomaly_detection`), so "forecast next
   quarter's sales" / "find statistically anomalous merchant groups" resolve to the
   real engines and become governed proposals through the existing agent.

## Implement & Test log

### DONE

Modules + executor wiring + catalog flip + agent hints as designed. `stats_forecast`
runs real AutoARIMA/AutoETS/AutoCES/AutoTheta with a backtest; `z_score_based_
anomaly_detection` runs the real weighted-composite z-score engine.

**Test:** `tests/unit/test_forecasting_anomaly.py` (13) — real horizons + backtest
MAE/RMSE across AutoARIMA/AutoETS/AutoTheta, prediction intervals bracket the point
forecast, fail-closed on short/bad series; z-score flags a stark outlier group across
all five metrics + a weighted composite, fail-closed on bad params. Retired-placeholder
tests updated (`test_algorithms.py`, `test_catalog_bootstrap.py` now assert z-score is
runnable). Full pipeline-orchestrator suite green (**152**); `model_training` suite
green. No regression.

### Live-verified (2026-07-23)

Drove the REAL `LocalTrainingExecutor` against running MLflow (:5500): a
`stats_forecast` run (AutoARIMA, season 12, horizon 6) logged real backtest
mae/rmse; a `z_score_based_anomaly_detection` run flagged the stark outlier group
(n_anomalies=1, rate=0.17, 6 groups). Both persisted real runs + result artifacts
in MLflow. Executor bugs found+fixed in commit 261b0f5.
