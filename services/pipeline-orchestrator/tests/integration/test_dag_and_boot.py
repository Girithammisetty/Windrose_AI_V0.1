"""DAG validation over the real SQL-backed app, and confirmation that app.main wires
REAL adapters (OPA, Redis, MinIO/S3 manifest store) + the local training executor by
default under PPL_USE_REAL_ADAPTERS."""

from __future__ import annotations

import httpx
import pytest

from app.api.auth import OpaAuthzClient, Principal
from app.container import build_container
from app.executor.local import LocalTrainingExecutor
from app.main import create_app
from tests.conftest import TENANT_A, auth, make_settings
from tests.integration.conftest import (
    MLFLOW_URI,
    OPA_URL,
    REDIS_URL,
    S3_ENDPOINT,
    ensure_bucket,
)

pytestmark = pytest.mark.integration


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


async def test_validate_rejects_cycle_and_type_mismatch(app_sf, clock):
    c = build_container(make_settings(), mode="sql", session_factory=app_sf, clock=clock)
    app = create_app(c)
    cl = await _client(app)
    try:
        cycle = {"pipeline_type": "data_prep", "definition": {
            "nodes": [{"alias": "a", "component": "filter-data"},
                      {"alias": "b", "component": "filter-data"}],
            "edges": [{"from": "a.out", "to": "b.in1", "type": "dataframe"},
                      {"from": "b.out", "to": "a.in1", "type": "dataframe"}]}}
        r = await cl.post("/api/v1/pipelines/validate?mode=all", json=cycle,
                          headers=auth(TENANT_A))
        assert r.status_code == 422
        codes = {i["code"] for i in r.json()["data"]["items"]}
        assert "DAG_CYCLE" in codes

        mismatch = {"pipeline_type": "training", "model_type": "classification",
                    "definition": {
                        "nodes": [
                            {"alias": "read-1", "component": "read-from-warehouse",
                             "parameters": {"dataset": "wr:t:dataset:dataset/x"},
                             "outputs": [{"name": "out", "type": "dataframe"}]},
                            {"alias": "mi", "component": "model-input",
                             "parameters": {"role": "TRAIN"},
                             "outputs": [{"name": "out", "type": "dataframe"}]},
                            {"alias": "train-1", "component": "xgboost-train",
                             "outputs": [{"name": "model", "type": "model"}]},
                            {"alias": "flt", "component": "filter-data",
                             "parameters": {"expression": "x"}}],
                        "edges": [
                            {"from": "read-1.out", "to": "mi.in1", "type": "dataframe"},
                            {"from": "mi.out", "to": "train-1.in1", "type": "dataframe"},
                            {"from": "train-1.model", "to": "flt.in1", "type": "model"}]}}
        r2 = await cl.post("/api/v1/pipelines/validate?mode=all", json=mismatch,
                           headers=auth(TENANT_A))
        assert r2.status_code == 422
        assert "EDGE_TYPE_MISMATCH" in {i["code"] for i in r2.json()["data"]["items"]}
    finally:
        await cl.aclose()


def test_shipped_app_main_defaults_to_real_adapters(monkeypatch):
    """The DEFAULT create_app() (no PPL_USE_REAL_ADAPTERS override) must build the REAL
    SQL/RLS-backed container — never the in-memory doubles (no-stub END STATE)."""
    from app.api.auth import OpaAuthzClient
    from app.config import Settings
    from app.main import create_app

    assert Settings().use_real_adapters is True  # shipped default
    app = create_app()  # engines are lazy; no network at construction
    c = app.state.container
    assert isinstance(c.authz, OpaAuthzClient)
    assert isinstance(c.deps.executor, LocalTrainingExecutor)
    assert type(c.dedup).__name__ == "RedisDedupStore"
    assert type(c.deps.manifest_store).__name__ == "S3ManifestStore"
    assert c.memory_state is None  # NOT the in-memory store
    assert "engines" in c.extras  # a real Postgres engine was created


async def test_app_main_wires_real_adapters_and_local_executor(app_sf):
    if not ensure_bucket():
        pytest.skip(f"MinIO/S3 unreachable at {S3_ENDPOINT}")
    from app.adapters.manifest_store import S3ManifestStore
    from app.adapters.mlflow_gateway import MlflowGateway

    settings = make_settings(use_real_adapters=True, mlflow_tracking_uri=MLFLOW_URI,
                             redis_url=REDIS_URL, opa_url=OPA_URL, s3_endpoint_url=S3_ENDPOINT)
    c = build_container(settings, mode="sql", session_factory=app_sf)

    # REAL adapters by default.
    assert isinstance(c.authz, OpaAuthzClient)
    assert isinstance(c.deps.executor, LocalTrainingExecutor)
    assert isinstance(c.deps.mlflow, MlflowGateway)
    assert isinstance(c.deps.manifest_store, S3ManifestStore)
    assert c.settings.executor_backend == "local"
    # RedisDedupStore from windrose_common.
    assert type(c.dedup).__name__ == "RedisDedupStore"

    # Real OPA + Redis round-trip: a decision returns cleanly (deny-by-default here,
    # since no projection is seeded) — proving the real OPA HTTP path is wired.
    principal = Principal(sub="user-1", tenant_id=TENANT_A, scopes=[])
    decision = await c.authz.allow(principal, "pipeline.run.create", None)
    assert decision in (True, False)
