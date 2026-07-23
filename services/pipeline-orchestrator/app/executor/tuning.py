"""BRD 63 — real hyperparameter search + cross-validation + wrapper feature
selection for the local training executor (closing M1/M5/M6).  runs grid /
random search over per-algorithm ranges with k-fold CV, refits the best, and can
bind a wrapper feature selector to the fit; this module gives Datacern the same,
built on scikit-learn's `GridSearchCV` / `RandomizedSearchCV` / `SequentialFeature
Selector`. Pure functions — the executor calls them; they own no state.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Per-algorithm search spaces (mirror  DEFAULT_RANGES; keys are the sklearn/
# xgboost estimator param names so they apply directly to the built estimator).
_SEARCH_SPACES: dict[str, dict[str, list]] = {
    "xgboost": {"n_estimators": [50, 100, 200], "max_depth": [3, 5, 7],
                "learning_rate": [0.03, 0.1, 0.3]},
    "xgboost_regressor": {"n_estimators": [50, 100, 200], "max_depth": [3, 5, 7],
                          "learning_rate": [0.03, 0.1, 0.3]},
    "random_forest": {"n_estimators": [100, 200, 400], "max_depth": [None, 6, 12],
                      "min_samples_leaf": [1, 2, 4]},
    "random_forest_regressor": {"n_estimators": [100, 200, 400], "max_depth": [None, 6, 12],
                                "min_samples_leaf": [1, 2, 4]},
    "decision_tree": {"max_depth": [None, 4, 8, 12], "min_samples_leaf": [1, 2, 4]},
    "decision_tree_regressor": {"max_depth": [None, 4, 8, 12], "min_samples_leaf": [1, 2, 4]},
    "logistic_regression": {"C": [0.1, 1.0, 10.0], "max_iter": [200, 500]},
    "knn": {"n_neighbors": [3, 5, 7, 11], "weights": ["uniform", "distance"]},
    "svm": {"C": [0.5, 1.0, 5.0], "kernel": ["rbf", "linear"]},
    "support_vector_regression": {"C": [0.5, 1.0, 5.0], "kernel": ["rbf", "linear"]},
    "light_gbm": {"num_leaves": [15, 31, 63], "learning_rate": [0.05, 0.1],
                  "n_estimators": [100, 200]},
    "kmeans": {"n_clusters": [2, 3, 4, 5, 8]},
}

# Default scoring per family (what the search optimizes).
_SCORING = {"classification": "f1_weighted", "regression": "r2"}


def search_space(algorithm: str) -> dict[str, list]:
    """The declared search space for an algorithm, or {} if none (single-fit)."""
    return dict(_SEARCH_SPACES.get(algorithm, {}))


def supports_search(algorithm: str) -> bool:
    return algorithm in _SEARCH_SPACES


def run_search(base_estimator, algorithm, X, y, *, kind="grid", n_trials=20,
               cv_folds=5, scoring=None, family="classification", random_state=42):
    """Run a REAL grid/random search with k-fold CV over the algorithm's space.

    Returns (best_estimator, best_params, cv_score). Falls back to a single fit
    (empty space / too few rows / no CV requested) — honestly, never faking a search.
    """
    from sklearn.model_selection import (
        GridSearchCV,
        ParameterSampler,
        RandomizedSearchCV,
    )

    space = search_space(algorithm)
    n_samples = len(X)
    folds = max(2, min(int(cv_folds or 5), n_samples))
    if not space or n_samples < folds * 2:
        base_estimator.fit(X, y)
        return base_estimator, {}, None

    scoring = scoring or _SCORING.get(family, "f1_weighted")
    if kind == "random":
        # Cap the sampled candidates at n_trials AND the grid's own cardinality.
        n_grid = 1
        for v in space.values():
            n_grid *= len(v)
        n_iter = max(1, min(int(n_trials or 20), n_grid))
        search = RandomizedSearchCV(
            base_estimator, space, n_iter=n_iter, cv=folds, scoring=scoring,
            random_state=random_state, refit=True, n_jobs=1, error_score="raise")
    else:
        search = GridSearchCV(
            base_estimator, space, cv=folds, scoring=scoring, refit=True,
            n_jobs=1, error_score="raise")
    search.fit(X, y)
    return (search.best_estimator_, dict(search.best_params_),
            float(search.best_score_))


def wrap_feature_selection(estimator, *, kind="sequential", n_features=None,
                           direction="forward", scoring=None, cv_folds=3):
    """Compose a wrapper feature selector in front of the estimator as an sklearn
    Pipeline, so the selected columns persist into the fitted/logged model. `kind`:
    'sequential' (SequentialFeatureSelector) | 'kbest' (SelectKBest f-stat).
    """
    from sklearn.feature_selection import (
        SelectKBest,
        SequentialFeatureSelector,
        f_classif,
    )
    from sklearn.pipeline import Pipeline

    if kind == "kbest":
        k = n_features or "all"
        selector = SelectKBest(score_func=f_classif, k=k)
    else:
        selector = SequentialFeatureSelector(
            estimator, n_features_to_select=(n_features or "auto"),
            direction=direction, cv=max(2, int(cv_folds or 3)), scoring=scoring, n_jobs=1)
    return Pipeline([("select", selector), ("model", estimator)])


def hpo_requested(params: dict) -> bool:
    """True when the run params ask for a search (search kind and/or CV folds>1)."""
    p = params or {}
    return str(p.get("search", "")).lower() in ("grid", "random") or int(
        p.get("cv_folds", 0) or 0) > 1


def feature_selection_requested(params: dict) -> bool:
    fs = (params or {}).get("feature_selection")
    return bool(fs) and str(fs).lower() not in ("none", "false", "0")
