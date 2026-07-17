"""The component registry + declarative algorithm templates as first-class catalog
objects (PIPE-FR-050/051/052).

These are seeded at startup (and on a signed catalog-manifest change in prod). The
data-prep component set and the 21 algorithm templates are the V1 catalog kept
verbatim in name/semantics; each component carries a real parameter schema + arity
metadata so validation (PIPE-FR-012/014) and compilation (PIPE-FR-021) are exact.
"""

from __future__ import annotations

from app.domain.entities import AlgorithmTemplate, Component

# component_type
IO = 0
DATA_PREP = 1
ALGORITHM = 2
UTILITY = 3
COMMENT = 4

CATALOG_VERSION = "windrose-catalog/1.0.0"

# The full data-prep component set (PIPE-FR-051), kept from the V1 catalog.
_DATA_PREP_NAMES = [
    "add-guid-column", "cast-data", "correlation-filter", "filter-data", "group-by",
    "handle-missing-values", "join-data", "linear-combination", "long-to-wide-converter",
    "merge-data", "minmax-scale", "one-hot-encoder", "ordinal-encoder", "pca",
    "python-expression", "quantization", "quasi-constant-filter", "remove-duplicate-rows",
    "remove-outliers", "rename-columns", "sample-data", "select-columns", "sort-data",
    "split-data", "statistical-filter", "target-encoder", "transform-data", "union",
    "variance-filter", "wide-to-long-converter", "zscore-normalization",
]

# Per-component arity + parameter overrides (defaults: 1 in / 1 out, dataframe ports).
_OVERRIDES: dict[str, dict] = {
    "split-data": {
        "max_outputs": 2,
        "outputs": [{"name": "train", "type": "dataframe"},
                    {"name": "test", "type": "dataframe"}],
        "parameters": {
            "split_size": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                           "required": True, "default": 0.8},
            "shuffle": {"type": "boolean", "required": False, "default": True},
        },
    },
    "join-data": {
        "min_inputs": 2, "max_inputs": 2,
        "parameters": {
            "join_type": {"type": "string", "format": "enum",
                          "enum": ["inner", "left", "outer"],
                          "required": True, "default": "inner"},
            # A column of the input dataset (data-aware: resolved against its schema).
            "on": {"type": "string", "format": "column", "required": True},
        },
    },
    "merge-data": {"min_inputs": 2, "max_inputs": 8},
    "union": {"min_inputs": 2, "max_inputs": 8},
    "filter-data": {
        "parameters": {"expression": {"type": "text", "format": "expression",
                                      "required": True}},
    },
    "select-columns": {
        "parameters": {"columns": {"type": "array", "format": "columns",
                                   "item_format": "column", "min_items": 1,
                                   "required": True, "item_description": "column name"}},
    },
    "handle-missing-values": {
        "parameters": {
            "strategy": {"type": "string", "enum": ["mean", "median", "most_frequent",
                                                    "constant", "drop"],
                         "required": True, "default": "mean"},
        },
    },
    "sample-data": {
        "parameters": {"n_rows": {"type": "int", "minimum": 1, "required": True}},
    },
    "one-hot-encoder": {
        "parameters": {"columns": {"type": "array", "min_items": 1, "required": True}},
    },
}


def _component(name: str, ctype: int, *, label: str | None = None,
               min_inputs: int = 1, max_inputs: int = 1, max_outputs: int = 1,
               outputs=None, parameters=None, guaranteed_qos: bool = False,
               internal: int = 0) -> Component:
    definition = {
        "min_inputs": min_inputs,
        "max_inputs": max_inputs,
        "max_outputs": max_outputs,
        "guaranteed_qos": guaranteed_qos,
        "outputs": outputs if outputs is not None else [{"name": "out",
                                                         "type": "dataframe"}],
        "parameters": parameters or {},
    }
    return Component(
        name=name, component_type=ctype, internal_component_type=internal,
        label=label or name.replace("-", " ").title(),
        definition=definition, yaml_ref=f"components/{name}/component.yaml",
        image_digest=f"sha256:{name:_>0}"[:71], catalog_version=CATALOG_VERSION,
        enabled=True,
    )


def seed_components() -> list[Component]:
    comps: list[Component] = []

    # IO components.
    comps.append(_component(
        "read-from-warehouse", IO, min_inputs=0, max_inputs=0, max_outputs=1,
        parameters={"dataset": {"type": "dataset_ref", "required": True}}))
    comps.append(_component(
        "write-to-warehouse", IO, min_inputs=1, max_inputs=1, max_outputs=0,
        outputs=[],
        parameters={"output_dataset_name": {"type": "restricted_string", "required": True,
                                            "min_length": 1, "max_length": 128}}))
    comps.append(_component(
        "batch-read-from-warehouse", IO, min_inputs=0, max_inputs=0, max_outputs=1,
        parameters={"dataset": {"type": "dataset_ref", "required": True}}))
    comps.append(_component(
        "batch-write-to-warehouse", IO, min_inputs=1, max_inputs=1, max_outputs=0,
        outputs=[],
        parameters={"output_dataset_name": {"type": "restricted_string", "required": True}}))

    # model-input: role-typed feed into training/tuning (PIPE-FR-015).
    comps.append(_component(
        "model-input", DATA_PREP, min_inputs=1, max_inputs=1, max_outputs=1,
        parameters={"role": {"type": "string", "enum": ["TRAIN", "VALIDATION", "TEST"],
                             "required": True}}))

    # Injected utility nodes (invisible in user definition; present in the manifest).
    comps.append(_component("clone-input", UTILITY, min_inputs=1, max_inputs=1,
                            max_outputs=16, internal=1))
    comps.append(_component("data-profiler", UTILITY, min_inputs=1, max_inputs=1,
                            max_outputs=1, internal=1))
    comps.append(_component("comment", COMMENT, min_inputs=0, max_inputs=0, max_outputs=0,
                            outputs=[]))

    # The data-prep set.
    for name in _DATA_PREP_NAMES:
        ov = _OVERRIDES.get(name, {})
        comps.append(_component(
            name, DATA_PREP,
            min_inputs=ov.get("min_inputs", 1), max_inputs=ov.get("max_inputs", 1),
            max_outputs=ov.get("max_outputs", 1), outputs=ov.get("outputs"),
            parameters=ov.get("parameters", {})))

    # The shared hyperparameter-search component (tune / cv variants).
    comps.append(_component(
        "hyperparameter-search", ALGORITHM, min_inputs=1, max_inputs=4, max_outputs=1,
        outputs=[{"name": "model", "type": "model"}], guaranteed_qos=True,
        parameters={
            "algorithm": {"type": "string", "required": True},
            "n_trials": {"type": "int", "minimum": 1, "maximum": 500, "required": False,
                         "default": 20},
            "cv_folds": {"type": "int", "minimum": 2, "maximum": 20, "required": False},
        }))

    # Per-algorithm native train components (train mode + clustering/forecasting).
    for name in _ALGORITHMS:
        comps.append(_component(
            f"{name}-train", ALGORITHM, min_inputs=1, max_inputs=2, max_outputs=1,
            outputs=[{"name": "model", "type": "model"}], guaranteed_qos=True,
            parameters=_ALGO_PARAMS.get(name, {})))
    return comps


# ---------------------------------------------------------------------------
# Algorithm templates (PIPE-FR-052) — the 21 declarative templates.
# ---------------------------------------------------------------------------

# model_type ints match ModelType enum.
_CLASSIFICATION, _REGRESSION, _ANOMALY, _FORECASTING, _CLUSTERING = 1, 2, 0, 3, 5

_ALGO_META: dict[str, tuple[int, str]] = {
    "agglomerative_clustering": (_CLUSTERING, "Agglomerative Clustering"),
    "dbscan": (_CLUSTERING, "DBSCAN"),
    "decision_tree": (_CLASSIFICATION, "Decision Tree"),
    "decision_tree_regressor": (_REGRESSION, "Decision Tree Regressor"),
    "isolation_forest": (_ANOMALY, "Isolation Forest"),
    "kmeans": (_CLUSTERING, "K-Means"),
    "knn": (_CLASSIFICATION, "K-Nearest Neighbors"),
    "light_gbm": (_CLASSIFICATION, "LightGBM"),
    "linear_regression": (_REGRESSION, "Linear Regression"),
    "logistic_regression": (_CLASSIFICATION, "Logistic Regression"),
    "mean_shift": (_CLUSTERING, "Mean Shift"),
    "naive_bayes": (_CLASSIFICATION, "Naive Bayes"),
    "one_class_svm": (_ANOMALY, "One-Class SVM"),
    "random_forest": (_CLASSIFICATION, "Random Forest"),
    "random_forest_regressor": (_REGRESSION, "Random Forest Regressor"),
    "stats_forecast": (_FORECASTING, "StatsForecast"),
    "support_vector_regression": (_REGRESSION, "Support Vector Regression"),
    "svm": (_CLASSIFICATION, "Support Vector Machine"),
    "xgboost": (_CLASSIFICATION, "XGBoost"),
    "xgboost_regressor": (_REGRESSION, "XGBoost Regressor"),
    "z_score_based_anomaly_detection": (_ANOMALY, "Z-Score Anomaly Detection"),
}

_ALGORITHMS = list(_ALGO_META.keys())

# Supervised algorithms get train/tune/cross_validation variants; clustering/anomaly/
# forecasting are unsupervised (no VALIDATION role, native train step reused).
_SUPERVISED = {
    "decision_tree", "decision_tree_regressor", "knn", "light_gbm", "linear_regression",
    "logistic_regression", "naive_bayes", "random_forest", "random_forest_regressor",
    "support_vector_regression", "svm", "xgboost", "xgboost_regressor",
}

_ALGO_PARAMS: dict[str, dict] = {
    "xgboost": {
        "n_estimators": {"type": "int", "minimum": 1, "maximum": 2000, "default": 200},
        "max_depth": {"type": "int", "minimum": 1, "maximum": 32, "default": 6},
        "learning_rate": {"type": "number", "minimum": 0.0001, "maximum": 1.0,
                          "default": 0.1},
    },
    "random_forest": {
        "n_estimators": {"type": "int", "minimum": 1, "maximum": 2000, "default": 200},
        "max_depth": {"type": "int", "minimum": 1, "maximum": 64},
    },
    "logistic_regression": {
        "C": {"type": "number", "minimum": 0.0001, "maximum": 1000.0, "default": 1.0},
        "max_iter": {"type": "int", "minimum": 10, "maximum": 10000, "default": 200},
    },
    "isolation_forest": {
        "n_estimators": {"type": "int", "minimum": 1, "maximum": 1000, "default": 100},
        "contamination": {"type": "number", "minimum": 0.0, "maximum": 0.5,
                          "default": 0.1},
    },
    "kmeans": {"n_clusters": {"type": "int", "minimum": 2, "maximum": 100, "default": 8}},
}


def _input_type(name: str) -> dict:
    if name in _SUPERVISED:
        return {"training": ["TRAIN"], "tuning": ["TRAIN", "VALIDATION"],
                "tuning_cross_validation": ["TRAIN"]}
    return {"training": ["TRAIN"], "tuning": ["TRAIN"], "tuning_cross_validation": ["TRAIN"]}


def seed_algorithm_templates() -> list[AlgorithmTemplate]:
    templates: list[AlgorithmTemplate] = []
    order = 0
    for name, (mtype, label) in _ALGO_META.items():
        order += 1
        # BR-14: z_score_based_anomaly_detection is a V1 placeholder — served,
        # not runnable.
        runnable = name != "z_score_based_anomaly_detection"
        train_body = {"train_component": f"{name}-train"} if runnable else {}
        tune_body = ({"train_component": "hyperparameter-search",
                      "algorithm": name} if runnable else {})
        templates.append(AlgorithmTemplate(
            name=name, label=label, model_type=mtype, order=order,
            model_type_order=mtype,
            input_type=_input_type(name),
            pipeline=train_body,
            tuning_pipeline=tune_body,
            tuning_pipeline_cross_validation=tune_body,
            parameters=_ALGO_PARAMS.get(name, {}),
            tuning_parameters=_ALGO_PARAMS.get(name, {}),
            metadata={"supervised": name in _SUPERVISED},
            catalog_version=CATALOG_VERSION, runnable=runnable))
    return templates
