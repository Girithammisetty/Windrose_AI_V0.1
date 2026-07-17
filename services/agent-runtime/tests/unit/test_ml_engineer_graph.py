"""ml-engineer LangGraph (BRD 52): autonomously launches sandboxed training
candidates, compares REAL run metrics, and emits the promotion as a WRITE
INTENT (never a direct write) — with honest failure reports when the inputs
or runs are unusable. Mirrors test_model_training_graph."""

from __future__ import annotations

from app.adapters.fakes import (
    FakeDatasetReader,
    FakeExperimentReader,
    FakeLlm,
    FakePipelineReader,
    FakePipelineWriter,
)
from app.graphs.base import GraphDeps
from app.graphs.ml_engineer import PROMOTE_TOOL_ID, run_ml_engineer
from tests.conftest import TENANT_A

_PLAN_JSON = (
    '{"candidates": {"xgboost": {"n_estimators": 5000, "max_depth": 4},'
    ' "random_forest": {"n_estimators": 100}},'
    ' "feature_columns": ["amount"],'
    ' "rationale": "Small tabular set: shallow boosted trees + a forest baseline."}'
)

_SCHEMA = {"version_no": 3, "row_count": 26,
           "schema": {"amount": {"type": "double"},
                      "disposition": {"type": "string"}}}

# The registry double: version 1 of model m-scorer traces to the first fake
# training run ("run-1" -> mlflow id "mlrun-run-1", see FakePipelineWriter/Reader).
_REGISTRY_MODELS = [{"id": "m-scorer", "name": "cd-disposition-scorer",
                     "urn": "wr:t:experiment:model/m-scorer"}]
_REGISTRY_DETAIL = {"model": _REGISTRY_MODELS[0],
                    "versions": [{"model_id": "m-scorer", "version": 1,
                                  "mlflow_run_id": "mlrun-run-1", "stage": "none"}]}


def _deps(**over):
    base = dict(
        llm=FakeLlm(content=_PLAN_JSON),
        dataset_reader=FakeDatasetReader(schema=_SCHEMA),
        pipeline_reader=FakePipelineReader(),
        pipeline_writer=FakePipelineWriter(),
        experiment_reader=FakeExperimentReader(models=_REGISTRY_MODELS,
                                               model=_REGISTRY_DETAIL),
        prompt_params={}, obo_token="tok")
    base.update(over)
    return GraphDeps(**base)


_INPUTS = {"tenant_id": TENANT_A, "workspace_id": "ws-1",
           "dataset": "auto-claims-latest", "label_column": "disposition"}


async def test_ml_engineer_trains_candidates_and_proposes_promotion():
    deps = _deps()
    outcome = await run_ml_engineer(deps, dict(_INPUTS))

    # It really launched the candidates (sandboxed writer), capped + planned.
    launched = [c["algorithm"] for c in deps.pipeline_writer.calls]
    assert launched == ["xgboost", "random_forest"]
    # Hyperparameters were schema-clamped (n_estimators max is 2000 in the fake).
    assert deps.pipeline_writer.calls[0]["params"]["n_estimators"] == 2000
    assert deps.pipeline_writer.calls[0]["params"]["label_column"] == "disposition"

    # The ONE consequential action is a write intent, not a direct write.
    wi = outcome.write_intent
    assert wi is not None
    assert wi.tool_id == PROMOTE_TOOL_ID == "experiment.model.promote"
    assert wi.tier == "write-proposal"
    assert wi.required_action == "experiment.model.update"
    assert wi.args["model_id"] == "m-scorer"
    assert wi.args["version"] == 1
    assert wi.args["target_stage"] == "staging"
    assert wi.workspace_id == "ws-1"

    # Evidence contract (MLE-FR-030): metrics copied verbatim, all candidates listed.
    assert "xgboost" in wi.rationale and "random_forest" in wi.rationale
    assert "f1=0.88" in wi.rationale
    assert outcome.structured["primary_metric"] == "f1"
    assert not outcome.structured["failed"]


async def test_ml_engineer_fails_honestly_on_unknown_target():
    outcome = await run_ml_engineer(_deps(), {**_INPUTS, "label_column": "nope"})
    assert outcome.write_intent is None
    assert outcome.structured["failed"] is True
    assert "not in the dataset schema" in outcome.final_text


async def test_ml_engineer_fails_honestly_without_writer():
    """Replay / writer-less mode plans but never trains or proposes."""
    outcome = await run_ml_engineer(_deps(pipeline_writer=None), dict(_INPUTS))
    assert outcome.write_intent is None
    assert outcome.structured["failed"] is True
    assert "disabled" in outcome.final_text


async def test_ml_engineer_no_proposal_without_registry_match(monkeypatch):
    """Trained + evaluated, but the registry has no matching version: report
    honestly instead of proposing an unresolvable promotion."""
    import app.graphs.ml_engineer as mod
    monkeypatch.setattr(mod, "_RESOLVE_INTERVAL_S", 0.0)  # don't wait between polls
    reader = FakeExperimentReader(
        models=_REGISTRY_MODELS,
        model={"model": _REGISTRY_MODELS[0], "versions": [
            {"model_id": "m-scorer", "version": 9, "mlflow_run_id": "other"}]})
    outcome = await run_ml_engineer(_deps(experiment_reader=reader), dict(_INPUTS))
    assert outcome.write_intent is None
    assert outcome.structured["failed"] is True
    assert "no matching registered model" in outcome.final_text


async def test_ml_engineer_resolves_when_mirror_catches_up(monkeypatch):
    """The registry mirror is eventually-consistent: the version is absent on the
    first poll and appears on a later one. The agent must retry, not give up."""
    import app.graphs.ml_engineer as mod
    monkeypatch.setattr(mod, "_RESOLVE_INTERVAL_S", 0.0)

    class _LaggyReader(FakeExperimentReader):
        def __init__(self):
            super().__init__(models=_REGISTRY_MODELS, model=_REGISTRY_DETAIL)
            self._get_calls = 0

        async def get_model(self, *, tenant_id, model_id, auth_token):
            self._get_calls += 1
            if self._get_calls < 3:  # mirror hasn't caught up yet
                return {"model": _REGISTRY_MODELS[0], "versions": []}
            return _REGISTRY_DETAIL

    outcome = await run_ml_engineer(_deps(experiment_reader=_LaggyReader()), dict(_INPUTS))
    assert outcome.write_intent is not None
    assert outcome.write_intent.args["model_id"] == "m-scorer"
    assert outcome.write_intent.args["version"] == 1
    assert not outcome.structured["failed"]
