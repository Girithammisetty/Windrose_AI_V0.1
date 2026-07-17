"""Shared unit fixtures: RSA-signed JWTs, fake clock, memory-mode app + client."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.container import build_container
from app.domain.services import CallCtx
from app.main import create_app

ISSUER = "https://identity.windrose.local"
AUDIENCE = "windrose"
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"

PIPE_MODEL = "wr:t:pipeline:pipeline/model-1"
PIPE_FE = "wr:t:pipeline:pipeline/fe-1"
PIPE_TRAIN = "wr:t:pipeline:pipeline/train-1"


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime.now(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode()
PUBLIC_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def make_token(tenant_id: str = TENANT_A, sub: str = "user-1", scopes=None,
               typ: str = "user", **extra) -> str:
    claims = {
        "sub": sub, "tenant_id": tenant_id, "typ": typ,
        "scopes": scopes if scopes is not None else ["*"], "iss": ISSUER, "aud": AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=5), "workspace_id": WORKSPACE, **extra,
    }
    return pyjwt.encode(claims, PRIVATE_PEM, algorithm="RS256")


def auth(tenant_id: str = TENANT_A, scopes=None, **extra) -> dict:
    return {"Authorization": f"Bearer {make_token(tenant_id, scopes=scopes, **extra)}"}


def make_settings() -> Settings:
    return Settings(use_real_adapters=False, jwt_public_key_pem=PUBLIC_PEM,
                    jwt_issuer=ISSUER, jwt_audience=AUDIENCE)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def container(clock):
    return build_container(make_settings(), mode="memory", clock=clock)


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def ctx_for(tenant_id: str = TENANT_A, sub: str = "user-1", via_agent=None,
            typ: str = "user", obo_sub: str | None = None) -> CallCtx:
    actor = {"type": "user", "id": sub}
    if typ == "agent_obo":
        actor = {"type": "user", "id": obo_sub or sub}
    return CallCtx(tenant_id=tenant_id, actor=actor, via_agent=via_agent,
                   workspace_id=WORKSPACE)


async def make_experiment(container, ctx: CallCtx, name: str = "Fraud") -> object:
    return await container.experiment_service.create(ctx, {
        "workspace_id": WORKSPACE, "name": name, "model_type": "classification",
        "model_pipeline_urn": PIPE_MODEL, "feature_engineering_pipeline_urn": PIPE_FE,
        "training_pipeline_urn": PIPE_TRAIN})


async def seed_finished_run(container, ctx: CallCtx, experiment_id: str, *,
                            mlflow_run_id: str, metrics: dict | None = None,
                            params: dict | None = None, algorithm: str = "xgboost",
                            finish: bool = True):
    run = await container.run_service.create_from_pipeline(ctx, {
        "mlflow_run_id": mlflow_run_id, "experiment_id": experiment_id,
        "algorithm": algorithm, "input_dataset_urns": [f"wr:{ctx.tenant_id}:dataset:dataset/ds1"]})
    await container.run_service.transition_status(ctx, "pipeline.run.started",
                                                  {"mlflow_run_id": mlflow_run_id})
    if finish:
        await container.run_service.transition_status(ctx, "pipeline.run.succeeded",
                                                      {"mlflow_run_id": mlflow_run_id})
    data = {"metrics": [{"key": k, "value": v, "step": 0, "timestamp": 1_700_000_000_000}
                        for k, v in (metrics or {}).items()],
            "params": [{"key": k, "value": v} for k, v in (params or {}).items()]}
    await container.mirror_service._apply_run_data(
        ctx, {"run_id": mlflow_run_id, "data": data})
    return run


def uid() -> str:
    return str(uuid.uuid4())
