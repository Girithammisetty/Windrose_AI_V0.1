"""Integration: REAL batch inference against live dev infra.

Loads a REAL model from the running MLflow registry and runs REAL scoring on a
REAL input parquet in MinIO, producing a REAL output dataset with REAL
predictions (INF-FR-004/030/032, AC-4). Also proves pre-submit schema-
incompatibility rejection (AC-1) and lineage edges (AC-4). Uses the real SQL UoW
(Postgres) + real MLflow + real MinIO. Auto-skips when infra is down."""

from __future__ import annotations

import pytest

from app.domain.ports import CallCtx
from app.domain.services import SubmitRequest
from tests.integration.conftest import (
    read_output_parquet,
    register_real_model,
    require_infra,
    seed_input_parquet,
    unique,
)

pytestmark = pytest.mark.integration

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "33333333-3333-4333-8333-333333333333"


def _ctx() -> CallCtx:
    return CallCtx(tenant_id=TENANT, actor={"type": "user", "id": "ds1", "scopes": ["*"]},
                   workspace_id=WORKSPACE, submitted_by="ds1")


async def _seed_input(container, *, urn: str, key: str, missing_age: bool = False):
    storage_uri = seed_input_parquet(key, rows=5, missing_age=missing_age)
    schema = {"amount": {"type": "double", "nullable": False}}
    if not missing_age:
        schema["age"] = {"type": "long", "nullable": False}
    async with container.deps.uow_factory(TENANT) as uow:
        await uow.inputs.upsert(
            urn=urn, dataset_id=urn.split("/")[-1], version_no=1, schema=schema,
            storage_uri=storage_uri, row_count=5, tenant_id=TENANT)


async def test_real_model_real_batch_inference_produces_real_predictions(real_container):
    require_infra((5432, "Postgres"), (5500, "MLflow"), (9000, "MinIO"), (6379, "Redis"))
    model_name = unique("fraud-real")
    version = register_real_model(model_name)
    model_urn = f"wr:{TENANT}:experiment:model_version/{model_name}@{version}"
    ds_urn = f"wr:{TENANT}:dataset:dataset/{unique('ds')}"
    await _seed_input(real_container, urn=ds_urn, key=f"it/{unique('in')}.parquet")

    ctx = _ctx()
    job = await real_container.inference.submit(ctx, SubmitRequest(model_urn, ds_urn))
    # real scoring: MLflow model load + predict + single-snapshot MinIO output
    await real_container.inference.execute_job(TENANT, job.id)

    fetched = await real_container.inference.get(ctx, job.id)
    assert fetched.status == 6, fetched.error  # succeeded
    assert fetched.output_dataset_urn is not None
    assert fetched.output_dataset_version == 1
    assert fetched.row_count == 5

    # REAL predictions: read the output parquet back from MinIO
    async with real_container.deps.uow_factory(TENANT) as uow:
        ver = await uow.outputs.version_for_job(job.id)
        storage_uri = ver.storage_uri
    out = read_output_parquet(storage_uri)
    assert "prediction" in out.columns
    assert len(out) == 5
    assert "_windrose_job_id" in out.columns
    assert set(out["prediction"].unique()) <= {0, 1}  # real classifier output

    # lineage edges model->job, input->job, job->output all present (AC-4)
    jurn = f"wr:{TENANT}:inference:job/{job.id}"
    async with real_container.deps.uow_factory(TENANT) as uow:
        edges = await uow.lineage.edges_touching(jurn, "both")
    activities = {e.activity for e in edges}
    assert {"used_by", "input_to", "produced"} <= activities


async def test_schema_incompatible_rejected_pre_submit_no_output(real_container):
    require_infra((5432, "Postgres"), (5500, "MLflow"), (9000, "MinIO"), (6379, "Redis"))
    model_name = unique("fraud-real")
    version = register_real_model(model_name)
    model_urn = f"wr:{TENANT}:experiment:model_version/{model_name}@{version}"
    ds_urn = f"wr:{TENANT}:dataset:dataset/{unique('ds')}"
    # dataset missing the required 'age' column
    await _seed_input(real_container, urn=ds_urn, key=f"it/{unique('bad')}.parquet",
                      missing_age=True)

    ctx = _ctx()
    job = await real_container.inference.submit(ctx, SubmitRequest(model_urn, ds_urn))
    assert job.status == 1  # rejected
    assert job.error["code"] == "SCHEMA_INCOMPATIBLE"
    assert any(d["name"] == "age" and d["verdict"] == "missing"
               for d in job.error["details"])
    # no output produced
    async with real_container.deps.uow_factory(TENANT) as uow:
        assert await uow.outputs.version_for_job(job.id) is None


async def test_production_models_uri_resolves_via_config_only(real_container, monkeypatch):
    """BUG-2: with NO MLflow env exported and the global uri pointed at a bogus
    local file store, the service still resolves + scores a promoted
    ``models:/<name>/Production`` model using only its own config (the adapter
    sets MLflow's global tracking+registry uri from config)."""
    require_infra((5432, "Postgres"), (5500, "MLflow"), (9000, "MinIO"), (6379, "Redis"))
    import mlflow

    model_name = unique("fraud-real")
    version = register_real_model(model_name)  # promoted to Production

    # simulate a fresh process with no env + a hostile global uri (local file store)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("MLFLOW_REGISTRY_URI", raising=False)
    mlflow.set_tracking_uri("file:///tmp/wr-bogus-mlruns")
    mlflow.set_registry_uri("file:///tmp/wr-bogus-mlruns")

    # resolve BY STAGE (Production) using only the service config — get_model_info
    # (global uri) would raise "Registered Model not found" against the file store
    resolved = await real_container.registry.resolve_by_stage(model_name, "production")
    assert resolved is not None
    assert resolved.version == version
    assert resolved.stage == "production"
    assert {c.name for c in resolved.inputs} == {"amount", "age"}

    # and it scores real data end to end (models:/ load resolves to the real server)
    ds_urn = f"wr:{TENANT}:dataset:dataset/{unique('ds')}"
    await _seed_input(real_container, urn=ds_urn, key=f"it/{unique('cfg')}.parquet")
    model_urn = f"wr:{TENANT}:experiment:model_version/{model_name}@{version}"
    ctx = _ctx()
    job = await real_container.inference.submit(ctx, SubmitRequest(model_urn, ds_urn))
    await real_container.inference.execute_job(TENANT, job.id)
    fetched = await real_container.inference.get(ctx, job.id)
    assert fetched.status == 6, fetched.error  # succeeded — real predictions written


async def test_stage_policy_denies_unpromoted(real_container):
    require_infra((5432, "Postgres"), (5500, "MLflow"), (9000, "MinIO"), (6379, "Redis"))
    from mlflow.tracking import MlflowClient

    model_name = unique("fraud-real")
    version = register_real_model(model_name)
    # demote to Archived so the default stage policy rejects it
    MlflowClient(tracking_uri="http://localhost:5500").transition_model_version_stage(
        model_name, str(version), "Archived")
    model_urn = f"wr:{TENANT}:experiment:model_version/{model_name}@{version}"
    ds_urn = f"wr:{TENANT}:dataset:dataset/{unique('ds')}"
    await _seed_input(real_container, urn=ds_urn, key=f"it/{unique('in')}.parquet")

    from app.domain.errors import ModelStageDenied

    with pytest.raises(ModelStageDenied):
        await real_container.inference.submit(_ctx(), SubmitRequest(model_urn, ds_urn))
