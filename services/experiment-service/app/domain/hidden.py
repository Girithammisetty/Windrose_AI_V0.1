"""Serving-layer display filtering of params (EXP-FR-005, BR-11).

Hidden params are ALWAYS stored (and always returned with ?include_hidden=true
and in audit exports) — this is presentation filtering only. The V1 hidden sets
are kept verbatim.
"""

from __future__ import annotations

HIDDEN_PARAMS: frozenset[str] = frozenset({
    "kubeflow_run_id", "classes", "flavor", "argo_workflow_name", "return_types",
    "include_features", "predict_proba", "search_strategy", "n_workers", "n_iter",
    "n_folds", "cross_validation", "average",
})

HIDDEN_PREFIXES: tuple[str, ...] = (
    "table_name", "model_dataset.", "set-model-params.", "write-to-warehouse.",
)

HIDDEN_SUFFIXES: tuple[str, ...] = (
    ".input_dataset", ".output_dataset", ".is_retry", ".table_name_prefix_uuid",
    ".view_name_prefix_uuid", ".view_name_prefix",
)


def is_hidden_param(key: str) -> bool:
    if key in HIDDEN_PARAMS:
        return True
    if any(key.startswith(p) for p in HIDDEN_PREFIXES):
        return True
    if any(key.endswith(s) for s in HIDDEN_SUFFIXES):
        return True
    return False
