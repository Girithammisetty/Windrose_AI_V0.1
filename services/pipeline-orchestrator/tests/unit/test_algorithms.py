"""Algorithm-template catalog + instantiation (PIPE-FR-052, AC-8, BR-14)."""

from __future__ import annotations

import pytest

from app.domain.algorithms import instantiate
from app.domain.catalog import seed_algorithm_templates
from app.domain.errors import ValidationFailed

ALGOS = {a.name: a for a in seed_algorithm_templates()}


def test_all_21_algorithm_templates_present():
    assert len(ALGOS) == 21


def test_ac8_xgboost_tune_requires_train_and_validation():
    xgb = ALGOS["xgboost"]
    definition = instantiate(xgb, mode="tune",
                             dataset_refs={"TRAIN": "wr:t:dataset:dataset/tr",
                                           "VALIDATION": "wr:t:dataset:dataset/va"},
                             params={})
    train_nodes = [n for n in definition["nodes"]
                   if n["component"] == "hyperparameter-search"]
    assert train_nodes and train_nodes[0]["parameters"]["algorithm"] == "xgboost"


def test_ac8_xgboost_tune_missing_validation_role_rejected():
    xgb = ALGOS["xgboost"]
    with pytest.raises(ValidationFailed) as exc:
        instantiate(xgb, mode="tune", dataset_refs={"TRAIN": "wr:t:dataset:dataset/tr"},
                    params={})
    assert "MISSING_MODEL_INPUT_ROLE: VALIDATION" in str(exc.value.message)


def test_zscore_anomaly_is_now_runnable():
    # BRD 64 (M3): z_score_based_anomaly_detection is a REAL statistical anomaly
    # engine now (was a BR-14 V1 placeholder) — it instantiates a train pipeline.
    z = ALGOS["z_score_based_anomaly_detection"]
    assert z.runnable is True
    definition = instantiate(z, mode="train",
                             dataset_refs={"TRAIN": "wr:t:dataset:dataset/tr"}, params={})
    assert any(n["component"] == "z_score_based_anomaly_detection-train"
               for n in definition["nodes"])


def test_train_mode_uses_native_train_component():
    rf = ALGOS["random_forest"]
    definition = instantiate(rf, mode="train",
                             dataset_refs={"TRAIN": "wr:t:dataset:dataset/tr"}, params={})
    assert any(n["component"] == "random_forest-train" for n in definition["nodes"])


@pytest.mark.asyncio
async def test_instantiate_endpoint_creates_valid_training_pipeline(client):
    from tests.conftest import WORKSPACE, auth

    body = {"workspace_id": WORKSPACE, "mode": "train",
            "dataset_refs": {"TRAIN": "wr:t:dataset:dataset/claims"},
            "parameters": {"label_column": "is_fraud"}}
    r = await client.post("/api/v1/algorithm-templates/xgboost/pipelines", json=body,
                          headers=auth())
    assert r.status_code == 201, r.text
    assert r.json()["data"]["validation_status"] == "valid"
