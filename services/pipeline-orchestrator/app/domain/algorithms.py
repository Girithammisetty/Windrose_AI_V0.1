"""Instantiate a training pipeline from a filled algorithm template (PIPE-FR-052/015).

Builds a typed DAG: one read-from-warehouse + model-input per required role, wired
into the algorithm's train node (native ``<name>-train`` for ``train`` mode and for
clustering/anomaly/forecasting; the shared ``hyperparameter-search`` with
``parameters.algorithm=<name>`` for the tune / cross_validation variants of
supervised algorithms). Reuses the same validation as UI submissions.
"""

from __future__ import annotations

from app.domain.entities import AlgorithmTemplate
from app.domain.errors import TemplateNotRunnable, ValidationFailed

_MODE_KEY = {
    "train": "training",
    "tune": "tuning",
    "cross_validation": "tuning_cross_validation",
}


def required_roles(algo: AlgorithmTemplate, mode: str) -> list[str]:
    key = _MODE_KEY.get(mode)
    if key is None:
        raise ValidationFailed(f"unknown mode {mode!r}", code="VALIDATION_FAILED")
    return list(algo.input_type.get(key, ["TRAIN"]))


def instantiate(
    algo: AlgorithmTemplate,
    *,
    mode: str,
    dataset_refs: dict[str, str],
    params: dict | None = None,
) -> dict:
    """Return a training-pipeline ``definition`` (typed DAG). Raises 422 when a
    required model-input role has no dataset ref (AC-8) or the template is a
    non-runnable placeholder (BR-14)."""
    if not algo.runnable:
        raise TemplateNotRunnable(
            f"algorithm template {algo.name!r} is not runnable",
            code="TEMPLATE_NOT_RUNNABLE")

    roles = required_roles(algo, mode)
    missing = [r for r in roles if r not in dataset_refs or not dataset_refs[r]]
    if missing:
        raise ValidationFailed(
            f"MISSING_MODEL_INPUT_ROLE: {missing[0]}",
            code="VALIDATION_FAILED",
            details=[{"alias": None, "field": "dataset_refs",
                      "problem": f"MISSING_MODEL_INPUT_ROLE: {r}"} for r in missing])

    supervised = bool(algo.metadata.get("supervised"))
    use_hps = mode in ("tune", "cross_validation") and supervised

    # Only the algorithm's declared hyperparameters belong on the train node;
    # pipeline-level globals (e.g. label_column) stay at run-parameter level.
    hyper = {k: v for k, v in (params or {}).items() if k in algo.parameters}

    nodes: list[dict] = []
    edges: list[dict] = []
    model_input_aliases: list[str] = []
    for role in roles:
        r = role.lower()
        read_alias = f"read-{r}"
        mi_alias = f"model-input-{r}"
        nodes.append({
            "alias": read_alias, "component": "read-from-warehouse",
            "parameters": {"dataset": dataset_refs[role]},
            "outputs": [{"name": "out", "type": "dataframe"}],
        })
        nodes.append({
            "alias": mi_alias, "component": "model-input",
            "parameters": {"role": role},
            "outputs": [{"name": "out", "type": "dataframe"}],
        })
        edges.append({"from": f"{read_alias}.out", "to": f"{mi_alias}.in1",
                      "type": "dataframe"})
        model_input_aliases.append(mi_alias)

    train_alias = "train-1"
    if use_hps:
        train_params = {"algorithm": algo.name, **hyper}
        if mode == "cross_validation":
            train_params.setdefault("cv_folds", 5)
        nodes.append({
            "alias": train_alias, "component": "hyperparameter-search",
            "parameters": train_params,
            "outputs": [{"name": "model", "type": "model"}],
        })
    else:
        nodes.append({
            "alias": train_alias, "component": f"{algo.name}-train",
            "parameters": dict(hyper),
            "outputs": [{"name": "model", "type": "model"}],
        })
    for i, mi in enumerate(model_input_aliases, start=1):
        edges.append({"from": f"{mi}.out", "to": f"{train_alias}.in{i}",
                      "type": "dataframe"})

    return {
        "metadata": {
            "description": f"{algo.label} ({mode})",
            "global_parameters": ["label_column"],
            "algorithm": algo.name,
            "mode": mode,
        },
        "nodes": nodes,
        "edges": edges,
    }
