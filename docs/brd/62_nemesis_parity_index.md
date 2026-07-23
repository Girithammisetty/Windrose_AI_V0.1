# BRD 62–65 — Nemesis → Datacern Pipeline & ML Parity (initiative index)

**Status:** in-progress — 2026-07-23
**Owner:** platform · **Driver:** cross-verification of legacy Nemesis (Argo + pandas
component platform, rich classical-ML catalog) against Datacern's rebuilt services
found real gaps where Nemesis has working production code and Datacern has nothing,
a placeholder, or code gated behind infra it lacks locally.

This index enumerates every gap as a tracked BRD. Each BRD follows the repo
convention (**Analysis → Design → Implement & Test**) and carries a **hard,
first-class acceptance criterion: the feature must be drivable by an AI agent**
(extend an existing `agent-runtime` graph, or add a new one), in proposal-mode
under the same four-eyes / WORM governance as every other governed write.

---

## The parity gaps (source: 2026-07-23 cross-verification)

| ID | Gap | Nemesis today | Datacern today | → BRD |
|----|-----|---------------|----------------|-------|
| P1 | Data-prep operators don't execute locally | 34 pandas operators run in prod (Argo) | 31 operators cataloged/validated/compiled, but the inline executor only runs **training**; per-operator transforms execute **only via infra-gated Argo** | **62** |
| P3 | `right` join | inner/outer/left/right | inner/left/outer | 62 |
| P4 | Missing-value methods | 8 (incl. linear_interpolation, expression, previous/next) | 5 | 62 |
| P5 | Stratified train/test split | `stratify_columns` + `random_state` | split_size + shuffle | 62 |
| M1 | Hyperparameter tuning/search | real grid + random search, k-fold CV, time-budgeted, refit best | declared, but local executor strips `n_trials`/`cv_folds` → single fit; no optuna/grid/random | **63** |
| M5 | Cross-validation | k-fold / predefined split | collapses with M1 | 63 |
| M6 | In-training feature selection | Sequential + Random wrapper selectors | filter operators only; none bound to training | 63 |
| M4 | Real LightGBM | `lightgbm.LGBMClassifier` | `HistGradientBoostingClassifier` (not the lib) | 63 |
| M8 | Regularized linear regression | Ridge / Lasso / ElasticNet | plain LinearRegression | 63 |
| M7 | Rich per-family eval metrics/artifacts | ROC curve, confusion matrix, Davies-Bouldin, Calinski-Harabasz, explained_variance | inline accuracy/f1/r2/rmse/silhouette only | 63 |
| M2 | Real time-series forecasting | Nixtla StatsForecast (AutoARIMA/ETS/CES/Theta, MSTL, exogenous, PI) | `stats_forecast` backed by LinearRegression | **64** |
| M3 | Statistical (z-score) anomaly engine | 8 metric components + composite score | non-runnable V1 placeholder | 64 |
| P2 | Warehouse write-back (Athena/BigQuery/Synapse) | production `warehouse_writer_{aws,gcp,azure}` | Iceberg bronze only; warehouse adapter is `ENotImplemented` stub | **65** |

Shared gaps NOT counted as regressions (Nemesis lacks them too): online/real-time
serving (KServe 501), classic-ML prediction/feature drift. Tracked as future notes
in the relevant BRD, not built here.

---

## BRD breakdown + agent plan

### BRD 62 — Local pipeline execution engine + operator parity (foundation)
Real in-process pandas executor for all 31 data-prep operators + IO, so a full
data-prep / feature-engineering pipeline runs **end-to-end locally without Argo**
(the single highest-leverage gap — unblocks the entire classic-pipeline domain on a
standard deployment). Folds in P3/P4/P5 operator deltas.
**Agent:** NEW `data_pipeline_builder` graph — grounds on the operator catalog +
dataset schema, composes a data-prep DAG from NL, PROPOSES it as a governed
pipeline (proposal-mode). (`model_training` already builds *training* pipelines;
nothing builds *data-prep* pipelines today.)

### BRD 63 — Classic-ML training completeness
Real HPO (grid + random search + k-fold/predefined CV, time-budgeted, refit+register
best), wrapper feature selection (sequential/random), real LightGBM, regularized
linear regression (ridge/lasso/elasticnet), and per-family evaluate components
(ROC/confusion-matrix/Davies-Bouldin/Calinski-Harabasz/explained_variance).
**Agent:** EXTEND `ml_engineer` + `model_training` — propose a tuning strategy
(search kind, budget, CV folds) + optional feature selection as part of the training
WriteIntent; rank tuned candidates on the richer metric set.

### BRD 64 — Forecasting & statistical anomaly detection
Real Nixtla StatsForecast (AutoARIMA/AutoETS/AutoCES/AutoTheta, MSTL, exogenous,
prediction intervals) replacing the LinearRegression stub, and the z-score
statistical anomaly engine (metric components: statistic/entropy/ratio/graph_density/
unique/simple_value + composite score) replacing the non-runnable placeholder.
**Agent:** NEW `forecasting` + `anomaly_detection` graphs (or task-typed extension
of `ml_engineer`) — pick horizon/season/model family (forecasting) or metric set +
thresholds (anomaly) grounded in schema, propose the run.

### BRD 65 — Warehouse write-back sinks (infra-gated)
Replace the `ENotImplemented` warehouse adapter with a real, config-flexible
write-back path to Athena/BigQuery/Synapse behind the existing swappable-backend
registry (real where locally testable; cloud legs honestly infra-gated, never faked).
**Agent:** EXTEND `inference_agent` / write-back tooling to target a warehouse sink.

---

## Status (2026-07-23)

| BRD | Gaps | State |
|-----|------|-------|
| **62** local pipeline execution + operator parity | P1, P3, P4, P5 | **inc1 DONE** (operators + local executor + parity params, 27 tests). inc2 (data_pipeline_builder agent + run-lifecycle persistence) pending — persistence needs the Iceberg-commit write path (ingestion-service), flagged infra follow-up. |
| **63** classic-ML training completeness | M1, M4, M5, M6, M7, M8 | **DONE** (real HPO/CV/feature-selection/LightGBM/regularized-linear/rich-metrics + `model_training` tuning proposals; 16 tests). |
| **64** forecasting + statistical anomaly | M2, M3 | **DONE** (real StatsForecast + z-score engine + agent hints; 13 tests). |
| **65** warehouse write-back sinks | P2 | pending — infra-gated (Athena/BigQuery/Synapse); lowest local value. |

**12 of 13 gaps closed at the code+unit-test level.** Remaining: P2 (BRD 65,
infra-gated), the BRD 62 `data_pipeline_builder` agent + run-lifecycle persistence,
and live-verification of 62–64 against the running stack.

## Sequencing
62 (foundation — everything classic-pipeline depends on it) → 63 (training depth) →
64 (forecasting/anomaly) → 65 (warehouse sinks, infra-gated, lowest local value).
Each BRD is independently shippable, unit + (where local-testable) live-verified, and
lands its agent support in the same increment.

## Non-negotiables (carried from engineering rules)
Real / no-stub / no-fake; e2e-testable on Mac (real pandas/sklearn/statsforecast — no
GPU/cloud needed for 62–64); infra-only legs flagged honestly; agent writes always
proposal-mode through `create_from_intent` (four-eyes + WORM); don't over-engineer.
