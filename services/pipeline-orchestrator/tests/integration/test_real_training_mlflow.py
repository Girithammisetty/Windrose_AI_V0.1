"""HERO PROOF (learning loop): human corrections in → a REAL trained model out.

Real Postgres (RLS) + real MLflow (:5500) + the real local training executor. We feed
``case.disposition_applied`` corrections (each a labeled claims row), assemble the
labeled dataset in Postgres, run a retrain via the local executor, and assert a REAL
xgboost model was trained and logged to real MLflow with metrics + a registered model
artifact. No mocks in the path."""

from __future__ import annotations

import pytest

from app.container import build_container
from app.domain.entities import CallCtx
from app.events.envelope import make_envelope
from tests.conftest import TENANT_A, WORKSPACE, make_settings
from tests.integration.conftest import MLFLOW_URI, mlflow_up

pytestmark = pytest.mark.integration

DATASET_URN = f"wr:{TENANT_A}:dataset:dataset/claims-fraud"
EXPERIMENT = "windrose-pipeline-it"


def _disposition(i: int):
    fraud = i % 2 == 0
    payload = {
        "dataset_urn": DATASET_URN, "dataset_version": 1, "row_pk": f"claim-{i}",
        "disposition": {"id": "d", "code": "FRD" if fraud else "OK",
                        "category": "fraud" if fraud else "legitimate"},
        "resolution_note": "analyst triage",
        # feature snapshot carried with the correction
        "features": {"amount": 9000 + i * 10 if fraud else 100 + i,
                     "prior_claims": 5 if fraud else 0,
                     "num_line_items": 12 if fraud else 3},
    }
    return make_envelope(event_type="case.disposition_applied", tenant_id=TENANT_A,
                         actor={"type": "user", "id": "analyst"},
                         resource_urn=f"wr:{TENANT_A}:case:case/{i}", payload=payload)


async def test_corrections_produce_a_real_model_in_mlflow(app_sf, clock):
    if not mlflow_up():
        pytest.skip(f"MLflow unreachable at {MLFLOW_URI}")

    settings = make_settings(mlflow_tracking_uri=MLFLOW_URI, mlflow_experiment=EXPERIMENT,
                             default_min_seconds_between_runs=0)
    c = build_container(settings, mode="sql", session_factory=app_sf, clock=clock)
    ctx = CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": "analyst"},
                  workspace_id=WORKSPACE)

    # 1) Corrections in → labeled examples assembled in Postgres.
    for i in range(24):
        await c.consumer.handle(_disposition(i))
    async with c.deps.uow_factory(TENANT_A) as uow:
        assembled = await uow.labeled_examples.count_for_dataset(DATASET_URN)
    assert assembled == 24

    # 2) Instantiate an xgboost training template + retrain over the labeled dataset.
    template, _ = await c.instantiation_service.instantiate_pipeline(
        ctx, "xgboost", mode="train", dataset_refs={"TRAIN": DATASET_URN},
        params={"n_estimators": 40, "max_depth": 3}, workspace_id=WORKSPACE,
        name="fraud-retrain")
    op_id, run = await c.run_service.create_run(
        ctx, template.id, {"labeled_dataset_urn": DATASET_URN, "label_column": "label",
                           "algorithm": "xgboost"})
    assert op_id.startswith("op_")
    assert run.mlflow_run_id  # BR-15: MLflow run created before submit

    final = await c.run_service.drive_run(TENANT_A, run.id)
    assert final.status == 4, final.error  # succeeded
    assert final.model_uri and final.metrics.get("accuracy") is not None
    assert final.metrics["train_rows"] > 0

    # 3) Assert the REAL MLflow run + model artifact exist in the tracking server.
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=MLFLOW_URI)
    mlflow_run = client.get_run(final.mlflow_run_id)
    assert mlflow_run.info.run_id == final.mlflow_run_id
    assert "accuracy" in mlflow_run.data.metrics
    assert mlflow_run.data.params.get("algorithm") == "xgboost"

    # mlflow>=3 stores models as first-class LoggedModels (own storage
    # location), not run artifacts — assert the model is loadable by URI
    # instead of listing the run's artifact directory.
    from mlflow.models import get_model_info
    info = get_model_info(final.model_uri)
    assert info.model_uri, f"model not resolvable at {final.model_uri}"

    reg_name = f"wr_{TENANT_A[:8]}_fraud-retrain"
    versions = client.search_model_versions(f"name='{reg_name}'")
    assert versions, f"no registered model version for {reg_name}"

    print(f"\nPROOF mlflow_run_id={final.mlflow_run_id} metrics={final.metrics} "
          f"model_uri={final.model_uri} registered_model={reg_name}")
