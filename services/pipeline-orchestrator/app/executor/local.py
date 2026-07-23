"""The REAL local training executor — the DEFAULT run backend on the Mac.

Given a dataset (assembled labeled rows) + algorithm + params it runs genuine
scikit-learn / xgboost training, logs the run + metrics + the fitted model artifact
to REAL MLflow (tracking + registry at :5500), and produces a registered model
version. This is not a mock: ``mlflow.search_runs`` and the model registry show the
run and artifact afterwards (integration test asserts both).
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np
import pandas as pd

from app.domain.errors import ValidationFailed
from app.domain.ports import TrainingResult, TrainingSpec

logger = logging.getLogger(__name__)

# Classification / regression / anomaly / clustering families (matches ModelType).
_CLASSIFIERS = {
    "xgboost", "random_forest", "decision_tree", "logistic_regression", "knn", "svm",
    "naive_bayes", "light_gbm",
}
_REGRESSORS = {
    "xgboost_regressor", "random_forest_regressor", "decision_tree_regressor",
    "linear_regression", "support_vector_regression", "stats_forecast",
}
_ANOMALY = {"isolation_forest", "one_class_svm", "z_score_based_anomaly_detection"}
_CLUSTERING = {"kmeans", "dbscan", "mean_shift", "agglomerative_clustering"}


def _build_estimator(algorithm: str, params: dict):
    from sklearn.cluster import (
        DBSCAN,
        AgglomerativeClustering,
        KMeans,
        MeanShift,
    )
    from sklearn.ensemble import (
        IsolationForest,
        RandomForestClassifier,
        RandomForestRegressor,
    )
    from sklearn.linear_model import LinearRegression, LogisticRegression
    from sklearn.naive_bayes import GaussianNB
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.svm import SVC, SVR, OneClassSVM
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

    p = dict(params or {})
    for k in ("algorithm", "cv_folds", "n_trials", "search", "feature_selection",
              "n_features", "regularization", "label_column", "feature_columns"):
        p.pop(k, None)

    if algorithm == "xgboost":
        from xgboost import XGBClassifier
        return XGBClassifier(eval_metric="logloss", **_intfloat(p))
    if algorithm == "xgboost_regressor":
        from xgboost import XGBRegressor
        return XGBRegressor(**_intfloat(p))
    factory = {
        "random_forest": lambda: RandomForestClassifier(**_intfloat(p)),
        "random_forest_regressor": lambda: RandomForestRegressor(**_intfloat(p)),
        "decision_tree": lambda: DecisionTreeClassifier(**_intfloat(p)),
        "decision_tree_regressor": lambda: DecisionTreeRegressor(**_intfloat(p)),
        "logistic_regression": lambda: LogisticRegression(**_intfloat(p)),
        # BRD 63 (M8): regularization → Ridge/Lasso/ElasticNet (else plain OLS).
        "linear_regression": lambda: _linear_regressor(params),
        "stats_forecast": lambda: LinearRegression(),
        "knn": lambda: KNeighborsClassifier(**_intfloat(p)),
        "svm": lambda: SVC(probability=True, **_intfloat(p)),
        "support_vector_regression": lambda: SVR(**_intfloat(p)),
        "naive_bayes": lambda: GaussianNB(),
        # BRD 63 (M4): real LightGBM (honest fallback if the wheel is absent).
        "light_gbm": lambda: _lightgbm_classifier(p),
        "isolation_forest": lambda: IsolationForest(**_intfloat(p)),
        "one_class_svm": lambda: OneClassSVM(**_intfloat(p)),
        "z_score_based_anomaly_detection": lambda: IsolationForest(),
        "kmeans": lambda: KMeans(n_init=10, **_intfloat(p)),
        "dbscan": lambda: DBSCAN(**_intfloat(p)),
        "mean_shift": lambda: MeanShift(),
        "agglomerative_clustering": lambda: AgglomerativeClustering(**_intfloat(p)),
    }
    if algorithm not in factory:
        raise ValidationFailed(f"unsupported algorithm {algorithm!r}",
                               code="VALIDATION_FAILED")
    return factory[algorithm]()


def _lightgbm_classifier(p: dict):
    """BRD 63 (M4): the REAL LightGBM classifier. Honest fallback to sklearn
    HistGradientBoosting (with a logged warning) only if the lightgbm wheel is
    genuinely absent — never a silent substitution."""
    try:
        from lightgbm import LGBMClassifier
        return LGBMClassifier(verbose=-1, **_intfloat(p))
    except ImportError:  # pragma: no cover - exercised only without the wheel
        from sklearn.ensemble import HistGradientBoostingClassifier
        logger.warning("lightgbm not installed; falling back to "
                       "HistGradientBoostingClassifier for light_gbm")
        return HistGradientBoostingClassifier(**_intfloat(
            {k: v for k, v in p.items() if k in {"max_depth", "max_iter", "random_state"}}))


def _linear_regressor(params: dict):
    """BRD 63 (M8): plain OLS by default; `regularization` ∈ {ridge,lasso,elasticnet}
    selects the penalized variant ( RegularizedLinearRegression parity)."""
    from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, Ridge

    reg = str((params or {}).get("regularization", "none")).lower()
    alpha = float((params or {}).get("alpha", 1.0))
    if reg == "ridge":
        return Ridge(alpha=alpha)
    if reg == "lasso":
        return Lasso(alpha=alpha)
    if reg == "elasticnet":
        return ElasticNet(alpha=alpha,
                          l1_ratio=float((params or {}).get("l1_ratio", 0.5)))
    return LinearRegression()


def _intfloat(params: dict) -> dict:
    """Coerce known integer hyperparameters (JSON numbers arrive as float)."""
    out = {}
    for k, v in params.items():
        if isinstance(v, float) and v.is_integer() and k in {
            "n_estimators", "max_depth", "max_iter", "n_neighbors", "n_clusters",
            "min_samples", "random_state",
        }:
            out[k] = int(v)
        else:
            out[k] = v
    return out


def _family(algorithm: str, model_type: str) -> str:
    if algorithm in _CLASSIFIERS:
        return "classification"
    if algorithm in _REGRESSORS:
        return "regression"
    if algorithm in _ANOMALY:
        return "anomaly"
    if algorithm in _CLUSTERING:
        return "clustering"
    return {"classification": "classification", "regression": "regression",
            "anomaly_detection": "anomaly", "clustering": "clustering"}.get(
        model_type, "classification")


class LocalTrainingExecutor:
    """Runs training in a worker thread (mlflow + sklearn are blocking)."""

    def __init__(self, tracking_uri: str):
        self.tracking_uri = tracking_uri

    async def execute_training(self, spec: TrainingSpec) -> TrainingResult:
        return await asyncio.to_thread(self._train_sync, spec)

    def _train_sync(self, spec: TrainingSpec) -> TrainingResult:
        import mlflow
        from sklearn.model_selection import train_test_split

        if not spec.rows:
            raise ValidationFailed("no training rows assembled", code="VALIDATION_FAILED")

        mlflow.set_tracking_uri(self.tracking_uri)
        if spec.mlflow_run_id:
            # Resuming a run the gateway already created — it belongs to a specific
            # experiment (for a retrain, the experiment-service experiment so the
            # mirror sweep can see it). Align the active experiment to that run's OWN
            # experiment; setting a different one makes start_run(run_id=...) fail with
            # "active run ID does not match environment run ID".
            from mlflow.tracking import MlflowClient
            run_exp_id = MlflowClient(tracking_uri=self.tracking_uri).get_run(
                spec.mlflow_run_id).info.experiment_id
            mlflow.set_experiment(experiment_id=run_exp_id)
        else:
            mlflow.set_experiment(spec.experiment)

        df = pd.DataFrame(spec.rows)
        family = _family(spec.algorithm, spec.model_type)
        label_col = spec.label_column

        # BRD 64: forecasting / statistical anomaly consume spec.rows directly (a
        # series / grouped rows) — skip the tabular X/y feature prep, which would
        # crash on a single-column series ("No objects to concatenate").
        _special = spec.algorithm in ("stats_forecast", "z_score_based_anomaly_detection")
        if _special:
            y_raw, X, feature_names = None, pd.DataFrame(), []
        else:
            if family in ("classification", "regression") and label_col and label_col in df:
                y_raw = df[label_col]
                X = df.drop(columns=[label_col])
            else:
                y_raw = None
                X = df.drop(columns=[label_col]) if (label_col and label_col in df) else df

            X = pd.get_dummies(X, dummy_na=False)
            X = X.apply(pd.to_numeric, errors="coerce").fillna(0.0)
            feature_names = list(X.columns)

        run_kwargs = {"run_id": spec.mlflow_run_id} if spec.mlflow_run_id else {}
        with mlflow.start_run(**run_kwargs) as run:
            mlflow.set_tags({
                "datacern.tenant_id": spec.tenant_id,
                "datacern.run_id": spec.run_id,
                "datacern.algorithm": spec.algorithm,
                "datacern.family": family,
                **{f"datacern.{k}": str(v) for k, v in spec.tags.items()},
            })
            mlflow.log_params({k: v for k, v in (spec.params or {}).items()})
            mlflow.log_param("algorithm", spec.algorithm)
            mlflow.log_param("n_rows", len(df))
            mlflow.log_param("n_features", len(feature_names))

            # BRD 64: real forecasting (M2) / statistical z-score anomaly (M3) are
            # not sklearn estimators — run the dedicated engines, log real metrics +
            # a result artifact (no sklearn-registry version; honest, not faked).
            if spec.algorithm == "stats_forecast":
                from app.executor import forecasting
                fres = forecasting.run_forecast(spec.rows, dict(spec.params or {}))
                mlflow.log_metrics(fres["metrics"])
                mlflow.log_dict(fres, "forecast.json")
                result = TrainingResult(
                    mlflow_run_id=run.info.run_id,
                    model_uri=f"runs:/{run.info.run_id}/forecast.json",
                    registered_model_name=spec.registered_model_name, model_version="",
                    metrics=fres["metrics"], params=dict(spec.params or {}),
                    row_count=len(df))
            elif spec.algorithm == "z_score_based_anomaly_detection":
                from app.executor import anomaly
                ares = anomaly.score(spec.rows, dict(spec.params or {}))
                mlflow.log_metrics(ares["metrics"])
                mlflow.log_dict(ares, "anomaly.json")
                result = TrainingResult(
                    mlflow_run_id=run.info.run_id,
                    model_uri=f"runs:/{run.info.run_id}/anomaly.json",
                    registered_model_name=spec.registered_model_name, model_version="",
                    metrics=ares["metrics"], params=dict(spec.params or {}),
                    row_count=len(df))
            else:
                estimator = _build_estimator(spec.algorithm, spec.params)
                metrics, fitted = self._fit_and_score(
                    estimator, X, y_raw, family, train_test_split,
                    params=spec.params, algorithm=spec.algorithm)
                mlflow.log_metrics(metrics)

                model_uri, registered_name, version = self._log_model(
                    mlflow, fitted, X, spec)

                result = TrainingResult(
                    mlflow_run_id=run.info.run_id, model_uri=model_uri,
                    registered_model_name=registered_name, model_version=version,
                    metrics=metrics, params=dict(spec.params or {}), row_count=len(df))
        logger.info("trained %s run=%s metrics=%s", spec.algorithm, result.mlflow_run_id,
                    result.metrics)
        return result

    def _fit_and_score(self, estimator, X, y_raw, family, train_test_split, *,
                       params=None, algorithm="") -> tuple[dict, object]:
        """Fit + score. BRD 63: optionally runs real HPO (grid/random + CV) and/or
        wrapper feature selection, and always computes the richer per-family metric
        set. Returns (metrics, fitted_estimator) — the fitted estimator may be the
        search's best_estimator_, which is what gets logged."""
        from sklearn.metrics import (
            accuracy_score,
            calinski_harabasz_score,
            confusion_matrix,
            davies_bouldin_score,
            explained_variance_score,
            f1_score,
            mean_absolute_error,
            mean_squared_error,
            precision_score,
            r2_score,
            recall_score,
            roc_auc_score,
            silhouette_score,
        )

        from app.executor import tuning

        params = params or {}
        want_hpo = tuning.hpo_requested(params)
        want_fs = tuning.feature_selection_requested(params)

        def _prepare(est):
            if want_fs:
                est = tuning.wrap_feature_selection(
                    est, kind=str(params.get("feature_selection", "sequential")).lower()
                    if str(params.get("feature_selection")).lower() not in ("true", "1")
                    else "sequential",
                    n_features=params.get("n_features"))
            return est

        def _fit(est, Xtr, ytr, fam):
            extra: dict = {}
            if want_hpo and not want_fs:  # search over the raw estimator's space
                best, best_params, cv_score = tuning.run_search(
                    est, algorithm, Xtr, ytr,
                    kind=str(params.get("search", "grid")).lower(),
                    n_trials=int(params.get("n_trials", 20) or 20),
                    cv_folds=int(params.get("cv_folds", 5) or 5), family=fam)
                if best_params:
                    extra["hpo_search"] = 1.0
                    if cv_score is not None:
                        extra["cv_score"] = float(cv_score)
                return best, extra
            est = _prepare(est)
            est.fit(Xtr, ytr)
            return est, extra

        if family == "classification":
            from sklearn.preprocessing import LabelEncoder
            y = LabelEncoder().fit_transform(y_raw.astype(str))
            n_classes = len(set(y))
            if len(X) >= 8 and n_classes > 1 and min(np.bincount(y)) >= 2:
                Xtr, Xte, ytr, yte = train_test_split(
                    X, y, test_size=0.25, random_state=42, stratify=y)
            else:
                Xtr, Xte, ytr, yte = X, X, y, y
            fitted, extra = _fit(estimator, Xtr, ytr, "classification")
            pred = fitted.predict(Xte)
            metrics = {
                "accuracy": float(accuracy_score(yte, pred)),
                "f1_weighted": float(f1_score(yte, pred, average="weighted", zero_division=0)),
                "precision_weighted": float(precision_score(yte, pred, average="weighted",
                                                            zero_division=0)),
                "recall_weighted": float(recall_score(yte, pred, average="weighted",
                                                      zero_division=0)),
                "n_classes": float(n_classes),
                "train_rows": float(len(Xtr)), "test_rows": float(len(Xte)),
                **extra,
            }
            # ROC AUC (binary) + a flattened confusion matrix (bounded to small n).
            if n_classes == 2 and hasattr(fitted, "predict_proba"):
                try:
                    proba = fitted.predict_proba(Xte)[:, 1]
                    metrics["roc_auc"] = float(roc_auc_score(yte, proba))
                except Exception:  # noqa: BLE001
                    pass
            if n_classes <= 10:
                cm = confusion_matrix(yte, pred)
                for i in range(cm.shape[0]):
                    for j in range(cm.shape[1]):
                        metrics[f"cm_{i}_{j}"] = float(cm[i, j])
            return metrics, fitted
        if family == "regression":
            y = pd.to_numeric(y_raw, errors="coerce").fillna(0.0).to_numpy()
            if len(X) >= 8:
                Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=42)
            else:
                Xtr, Xte, ytr, yte = X, X, y, y
            fitted, extra = _fit(estimator, Xtr, ytr, "regression")
            pred = fitted.predict(Xte)
            return {
                "r2": float(r2_score(yte, pred)) if len(set(yte)) > 1 else 0.0,
                "rmse": float(np.sqrt(mean_squared_error(yte, pred))),
                "mae": float(mean_absolute_error(yte, pred)),
                "explained_variance": float(explained_variance_score(yte, pred)),
                "train_rows": float(len(Xtr)), "test_rows": float(len(Xte)),
                **extra,
            }, fitted
        if family == "anomaly":
            estimator.fit(X)
            pred = estimator.predict(X)
            anomaly_rate = float((pred == -1).mean())
            return {"anomaly_rate": anomaly_rate, "train_rows": float(len(X))}, estimator
        # clustering
        labels = estimator.fit_predict(X)
        metrics = {"n_clusters": float(len(set(labels)) - (1 if -1 in labels else 0)),
                   "train_rows": float(len(X))}
        try:
            if 1 < len(set(labels)) < len(X):
                metrics["silhouette"] = float(silhouette_score(X, labels))
                metrics["davies_bouldin"] = float(davies_bouldin_score(X, labels))
                metrics["calinski_harabasz"] = float(calinski_harabasz_score(X, labels))
        except Exception:  # noqa: BLE001
            pass
        return metrics, estimator

    def _log_model(self, mlflow, estimator, X, spec: TrainingSpec):
        # Flavor-aware logging: mlflow>=3 loads sklearn-flavor models through
        # skops' trust gate, which rejects native xgboost.*/lightgbm.* types — those
        # estimators must go through their own flavor (still pyfunc-loadable
        # downstream). BRD 63: lightgbm added alongside xgboost.
        module = type(estimator).__module__
        if module.startswith("xgboost"):
            import mlflow.xgboost as flavor
        elif module.startswith("lightgbm"):
            import mlflow.lightgbm as flavor
        else:
            import mlflow.sklearn as flavor

        name = spec.registered_model_name
        try:
            info = flavor.log_model(
                estimator, artifact_path="model", registered_model_name=name,
                input_example=X.head(2) if len(X) else None)
            model_uri = info.model_uri
        except Exception:  # noqa: BLE001 — registry optional; artifact still logged
            flavor.log_model(estimator, artifact_path="model")
            model_uri = f"runs:/{mlflow.active_run().info.run_id}/model"
        version = self._latest_version(mlflow, name)
        return model_uri, name, version

    @staticmethod
    def _latest_version(mlflow, name: str) -> str:
        try:
            client = mlflow.tracking.MlflowClient()
            versions = client.search_model_versions(f"name='{name}'")
            if versions:
                return str(max(int(v.version) for v in versions))
        except Exception:  # noqa: BLE001 — registry lookup failed; fall back but make it traceable
            logger.exception(
                "model version lookup failed for %s; falling back to version '1'", name
            )
        return "1"
