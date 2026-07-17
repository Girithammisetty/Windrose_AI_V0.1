"""Shared fixtures: RSA-signed JWTs, fake clock, in-memory app + client, and
unit-tier test doubles for the executor + MLflow gateway (doubles live ONLY here,
never in runtime wiring — CONVENTIONS.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.container import build_container
from app.domain.ports import TrainingResult
from app.main import create_app

ISSUER = "https://identity.windrose.local"
AUDIENCE = "windrose"
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime.now(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs):
        self._now += timedelta(**kwargs)


class FakeMlflow:
    def __init__(self):
        self.created = []
        self.terminated = []

    async def create_run(self, *, tags, experiment_id=None, experiment_name=None):
        rid = f"mlflow-{len(self.created)}"
        self.created.append((rid, tags, experiment_id, experiment_name))
        return rid

    async def set_terminated(self, run_id, status):
        self.terminated.append((run_id, status))


class FakeExecutor:
    """Records training specs and returns a deterministic result without MLflow.
    (Real training + real MLflow are exercised in the integration tier.)"""

    def __init__(self, fail: bool = False):
        self.specs = []
        self.fail = fail

    async def execute_training(self, spec) -> TrainingResult:
        self.specs.append(spec)
        if self.fail:
            raise RuntimeError("boom: simulated training failure")
        acc = 0.9 if spec.rows else 0.0
        return TrainingResult(
            mlflow_run_id=spec.mlflow_run_id or "mlflow-fake",
            model_uri=f"runs:/{spec.mlflow_run_id}/model",
            registered_model_name=spec.registered_model_name, model_version="1",
            metrics={"accuracy": acc, "train_rows": float(len(spec.rows))},
            params=spec.params, row_count=len(spec.rows))


_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode()
PUBLIC_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def make_token(tenant_id=TENANT_A, sub="user-1", scopes=None, typ="user", **extra):
    claims = {"sub": sub, "tenant_id": tenant_id, "typ": typ,
              "scopes": scopes if scopes is not None else ["*"], "iss": ISSUER,
              "aud": AUDIENCE, "exp": datetime.now(UTC) + timedelta(minutes=5),
              "workspace_id": WORKSPACE, **extra}
    return pyjwt.encode(claims, PRIVATE_PEM, algorithm="RS256")


def auth(tenant_id=TENANT_A, scopes=None, **extra):
    return {"Authorization": f"Bearer {make_token(tenant_id, scopes=scopes, **extra)}"}


def make_settings(tmp_path=None, **overrides) -> Settings:
    # Tests opt OUT of real adapters (the runtime default is real). The in-memory
    # doubles are reachable only here, never from the shipped app.main wiring.
    kw = dict(jwt_public_key_pem=PUBLIC_PEM, jwt_issuer=ISSUER, jwt_audience=AUDIENCE,
              use_real_adapters=False)
    if tmp_path is not None:
        kw["object_store_dir"] = str(tmp_path / "objects")
    kw.update(overrides)
    return Settings(**kw)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def fake_mlflow():
    return FakeMlflow()


@pytest.fixture
def fake_executor():
    return FakeExecutor()


@pytest.fixture
def container(tmp_path, clock, fake_mlflow, fake_executor):
    return build_container(make_settings(tmp_path), mode="memory", clock=clock,
                           executor=fake_executor, mlflow=fake_mlflow)


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---- definition helpers ----

def data_prep_definition(dataset="wr:t:dataset:dataset/ds-1", out_name="churn_features"):
    return {
        "metadata": {"description": "churn features"},
        "nodes": [
            {"alias": "read-1", "component": "read-from-warehouse",
             "parameters": {"dataset": dataset},
             "outputs": [{"name": "out", "type": "dataframe"}]},
            {"alias": "write-1", "component": "write-to-warehouse",
             "parameters": {"output_dataset_name": out_name}, "outputs": []}],
        "edges": [{"from": "read-1.out", "to": "write-1.in1", "type": "dataframe"}]}


async def create_template(client, *, name="churn-prep", pipeline_type="data_prep",
                          definition=None, tenant=TENANT_A, **body):
    payload = {"workspace_id": WORKSPACE, "name": name, "pipeline_type": pipeline_type,
               "definition": definition or data_prep_definition(), **body}
    resp = await client.post("/api/v1/pipelines", json=payload, headers=auth(tenant))
    return resp
