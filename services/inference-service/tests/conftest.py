"""Shared fixtures: RSA-signed JWTs, fake clock, in-memory doubles, memory app.

Doubles (FakeRegistry, FakeExecutor) live ONLY in tests and are never reachable
from app.main (CONVENTIONS.md).
"""

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
from app.domain.ports import ResolvedDataset, ResolvedModel, ScoringResult
from app.domain.schema_compat import ModelInputColumn
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

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


class FakeRegistry:
    """In-memory model registry double."""

    def __init__(self):
        self.models: dict[tuple[str, int], ResolvedModel] = {}
        self.by_stage: dict[tuple[str, str], ResolvedModel] = {}

    def add(self, name: str, version: int, *, stage: str,
            inputs: list[ModelInputColumn]):
        rm = ResolvedModel(
            name=name, version=version, stage=stage,
            model_uri=f"models:/{name}/{version}", inputs=inputs, model_id=name,
            run_id=f"run-{name}-{version}")
        self.models[(name, version)] = rm
        if stage != "none":
            self.by_stage[(name, stage)] = rm
        return rm

    async def resolve_version(self, name: str, version: int) -> ResolvedModel:
        rm = self.models.get((name, int(version)))
        if rm is None:
            raise LookupError(f"{name}@{version}")
        return rm

    async def resolve_by_stage(self, name: str, stage: str) -> ResolvedModel | None:
        return self.by_stage.get((name, stage))


class FakeExecutor:
    """Scoring executor double: records runs, returns a deterministic result."""

    def __init__(self, *, fail: bool = False):
        self.runs: list = []
        self.fail = fail

    async def run(self, *, model: ResolvedModel, dataset: ResolvedDataset, job,
                  parameters: dict) -> ScoringResult:
        self.runs.append(job.id)
        if self.fail:
            raise RuntimeError("scoring component failed")
        return ScoringResult(
            output_storage_uri=f"mem://scores/{job.id}.parquet",
            snapshot_id=uuid.uuid4().hex, row_count=dataset.row_count or 3,
            prediction_columns=["prediction"])


_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode()
PUBLIC_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def make_token(tenant_id: str = TENANT_A, sub: str = "user-1",
               scopes: list[str] | None = None, typ: str = "user",
               workspace_id: str = WORKSPACE, **extra) -> str:
    claims = {
        "sub": sub, "tenant_id": tenant_id, "typ": typ,
        "scopes": scopes if scopes is not None else ["*"],
        "workspace_id": workspace_id, "iss": ISSUER, "aud": AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=5), **extra,
    }
    return pyjwt.encode(claims, PRIVATE_PEM, algorithm="RS256")


def auth(tenant_id: str = TENANT_A, scopes: list[str] | None = None, **extra) -> dict:
    return {"Authorization": f"Bearer {make_token(tenant_id, scopes=scopes, **extra)}"}


def make_settings() -> Settings:
    return Settings(
        use_real_adapters=False, jwt_public_key_pem=PUBLIC_PEM, jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def registry():
    reg = FakeRegistry()
    reg.add("fraud-xgb", 3, stage="production", inputs=[
        ModelInputColumn("amount", "double", required=False),
        ModelInputColumn("age", "long", required=False),
        ModelInputColumn("merchant_id", "string", required=False),
    ])
    return reg


@pytest.fixture
def executor():
    return FakeExecutor()


@pytest.fixture
def container(clock, registry, executor):
    return build_container(make_settings(), mode="memory", clock=clock,
                           registry=registry, executor=executor)


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def add_input_dataset(container, *, urn: str, tenant_id: str = TENANT_A,
                      schema: dict | None = None, row_count: int = 3, version: int = 1):
    """Seed an input dataset into the memory store (test helper)."""
    schema = schema or {
        "amount": {"type": "double", "nullable": False},
        "age": {"type": "long", "nullable": False},
        "merchant_id": {"type": "string", "nullable": False},
    }
    container.memory_state.inputs[urn] = ResolvedDataset(
        urn=urn, dataset_id=urn.split("/")[-1], version=version, schema=schema,
        row_count=row_count, storage_uri=f"s3://windrose-datasets/{urn}.parquet")
