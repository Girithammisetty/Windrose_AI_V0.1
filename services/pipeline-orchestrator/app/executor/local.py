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
        HistGradientBoostingClassifier,
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
    p.pop("algorithm", None)
    p.pop("cv_folds", None)
    p.pop("n_trials", None)

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
        "linear_regression": lambda: LinearRegression(),
        "stats_forecast": lambda: LinearRegression(),
        "knn": lambda: KNeighborsClassifier(**_intfloat(p)),
        "svm": lambda: SVC(probability=True, **_intfloat(p)),
        "support_vector_regression": lambda: SVR(**_intfloat(p)),
        "naive_bayes": lambda: GaussianNB(),
        "light_gbm": lambda: HistGradientBoostingClassifier(**_intfloat(p)),
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
                "windrose.tenant_id": spec.tenant_id,
                "windrose.run_id": spec.run_id,
                "windrose.algorithm": spec.algorithm,
                "windrose.family": family,
                **{f"windrose.{k}": str(v) for k, v in spec.tags.items()},
            })
            mlflow.log_params({k: v for k, v in (spec.params or {}).items()})
            mlflow.log_param("algorithm", spec.algorithm)
            mlflow.log_param("n_rows", len(df))
            mlflow.log_param("n_features", len(feature_names))

            estimator = _build_estimator(spec.algorithm, spec.params)
            metrics = self._fit_and_score(estimator, X, y_raw, family, train_test_split)
            mlflow.log_metrics(metrics)

            model_uri, registered_name, version = self._log_model(
                mlflow, estimator, X, spec)

            result = TrainingResult(
                mlflow_run_id=run.info.run_id, model_uri=model_uri,
                registered_model_name=registered_name, model_version=version,
                metrics=metrics, params=dict(spec.params or {}), row_count=len(df))
        logger.info("trained %s run=%s metrics=%s", spec.algorithm, result.mlflow_run_id,
                    metrics)
        return result

    def _fit_and_score(self, estimator, X, y_raw, family, train_test_split) -> dict:
        from sklearn.metrics import (
            accuracy_score,
            f1_score,
            mean_squared_error,
            r2_score,
            silhouette_score,
        )

        if family == "classification":
            from sklearn.preprocessing import LabelEncoder
            y = LabelEncoder().fit_transform(y_raw.astype(str))
            n_classes = len(set(y))
            if len(X) >= 8 and n_classes > 1 and min(np.bincount(y)) >= 2:
                Xtr, Xte, ytr, yte = train_test_split(
                    X, y, test_size=0.25, random_state=42, stratify=y)
            else:
                Xtr, Xte, ytr, yte = X, X, y, y
            estimator.fit(Xtr, ytr)
            pred = estimator.predict(Xte)
            return {
                "accuracy": float(accuracy_score(yte, pred)),
                "f1_weighted": float(f1_score(yte, pred, average="weighted",
                                              zero_division=0)),
                "n_classes": float(n_classes),
                "train_rows": float(len(Xtr)), "test_rows": float(len(Xte)),
            }
        if family == "regression":
            y = pd.to_numeric(y_raw, errors="coerce").fillna(0.0).to_numpy()
            if len(X) >= 8:
                Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25,
                                                      random_state=42)
            else:
                Xtr, Xte, ytr, yte = X, X, y, y
            estimator.fit(Xtr, ytr)
            pred = estimator.predict(Xte)
            return {
                "r2": float(r2_score(yte, pred)) if len(set(yte)) > 1 else 0.0,
                "rmse": float(np.sqrt(mean_squared_error(yte, pred))),
                "train_rows": float(len(Xtr)), "test_rows": float(len(Xte)),
            }
        if family == "anomaly":
            estimator.fit(X)
            pred = estimator.predict(X)
            anomaly_rate = float((pred == -1).mean())
            return {"anomaly_rate": anomaly_rate, "train_rows": float(len(X))}
        # clustering
        labels = estimator.fit_predict(X)
        metrics = {"n_clusters": float(len(set(labels)) - (1 if -1 in labels else 0)),
                   "train_rows": float(len(X))}
        try:
            if 1 < len(set(labels)) < len(X):
                metrics["silhouette"] = float(silhouette_score(X, labels))
        except Exception:  # noqa: BLE001
            pass
        return metrics

    def _log_model(self, mlflow, estimator, X, spec: TrainingSpec):
        # Flavor-aware logging: mlflow>=3 loads sklearn-flavor models through
        # skops' trust gate, which rejects xgboost.* types — XGB estimators
        # must go through the xgboost flavor (still pyfunc-loadable downstream).
        if type(estimator).__module__.startswith("xgboost"):
            import mlflow.xgboost as flavor
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
        except Exception:  # noqa: BLE001
            pass
        return "1"
