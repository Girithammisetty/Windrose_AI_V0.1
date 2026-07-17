"""Shared fixtures: RSA-signed JWTs, fake clock, memory-mode app + client."""

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
from app.main import create_app

ISSUER = "https://identity.windrose.local"
AUDIENCE = "windrose"
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"
SPIFFE_INGESTION = "spiffe://windrose/ns/data/sa/ingestion-service"
SPIFFE_PROFILER = "spiffe://windrose/ns/data/sa/profiler"


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime.now(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


class RecordingRunner:
    """ProfilerRunner fake that records launches without running (AC-1/AC-3)."""

    def __init__(self):
        self.specs = []
        self.killed = []

    async def launch(self, spec):
        self.specs.append(spec)

    async def kill(self, profile_id):
        self.killed.append(profile_id)


_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    .decode()
)


def make_token(
    tenant_id: str = TENANT_A,
    sub: str = "user-1",
    scopes: list[str] | None = None,
    typ: str = "user",
    **extra,
) -> str:
    claims = {
        "sub": sub,
        "tenant_id": tenant_id,
        "typ": typ,
        "scopes": scopes if scopes is not None else ["*"],
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        **extra,
    }
    return pyjwt.encode(claims, PRIVATE_PEM, algorithm="RS256")


def auth(tenant_id: str = TENANT_A, scopes: list[str] | None = None, **extra) -> dict:
    return {"Authorization": f"Bearer {make_token(tenant_id, scopes=scopes, **extra)}"}


def make_settings(tmp_path) -> Settings:
    # The unit tier pins use_real_adapters=False explicitly (the RUNTIME
    # default is True, per CONVENTIONS.md rule 1) so the local doubles are
    # reachable only from tests.
    return Settings(
        jwt_public_key_pem=PUBLIC_PEM,
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        object_store_dir=str(tmp_path / "objects"),
        catalog_dir=str(tmp_path / "catalog"),
        use_real_adapters=False,
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def container(settings, clock):
    return build_container(settings, mode="memory", clock=clock)


@pytest.fixture
def recording_container(settings, clock):
    return build_container(settings, mode="memory", clock=clock, runner=RecordingRunner())


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
def recording_app(recording_container):
    return create_app(recording_container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def recording_client(recording_app):
    transport = httpx.ASGITransport(app=recording_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def ingestion_envelope(
    tenant_id: str,
    ingestion_id: str,
    *,
    dataset_name: str = "Orders",
    iceberg_table: str = "bronze.t.orders",
    snapshot_id: int = 1001,
    schema: dict | None = None,
    event_id: str | None = None,
    event_type: str = "ingestion.completed",
    skip_profiling: bool = False,
    connection_urn: str | None = None,
    **payload_extra,
) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "tenant_id": tenant_id,
        "actor": {"type": "service", "id": "ingestion-service"},
        "via_agent": None,
        "resource_urn": f"wr:{tenant_id}:ingestion:ingestion/{ingestion_id}",
        "occurred_at": datetime.now(UTC).isoformat(),
        "trace_id": "trace-test",
        "payload": {
            "ingestion_id": ingestion_id,
            "workspace_id": WORKSPACE,
            "dataset_name": dataset_name,
            "iceberg_table": iceberg_table,
            "iceberg_snapshot_id": snapshot_id,
            "schema": schema
            or {"order_id": {"type": "long", "nullable": False, "tags": []}},
            "row_count": 10,
            "bytes": 1024,
            "skip_profiling": skip_profiling,
            "connection_urn": connection_urn,
            **payload_extra,
        },
    }


async def create_dataset(client, tenant=TENANT_A, name="Orders", **body) -> dict:
    resp = await client.post(
        "/api/v1/datasets",
        json={"workspace_id": WORKSPACE, "name": name, **body},
        headers=auth(tenant),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]
