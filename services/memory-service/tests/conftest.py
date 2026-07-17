"""Shared fixtures: RSA-signed JWTs, fake clock, memory-mode app + client."""

from __future__ import annotations

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
USER_A = "user-alice"
USER_B = "user-bob"


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

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


def make_token(tenant_id=TENANT_A, sub=USER_A, scopes=None, typ="user", **extra) -> str:
    claims = {
        "sub": sub, "tenant_id": tenant_id, "typ": typ,
        "scopes": scopes if scopes is not None else ["*"],
        "iss": ISSUER, "aud": AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=5), **extra,
    }
    return pyjwt.encode(claims, PRIVATE_PEM, algorithm="RS256")


def auth(tenant_id=TENANT_A, sub=USER_A, scopes=None, **extra) -> dict:
    return {"Authorization": f"Bearer {make_token(tenant_id, sub, scopes, **extra)}"}


def make_settings(**over) -> Settings:
    # Tests default to the in-memory doubles (use_real_adapters=False); the
    # RUNTIME default is True. Integration tests opt back into real adapters
    # explicitly (e.g. real Kafka/Ollama).
    over.setdefault("use_real_adapters", False)
    return Settings(jwt_public_key_pem=PUBLIC_PEM, jwt_issuer=ISSUER,
                    jwt_audience=AUDIENCE, **over)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def container(settings, clock):
    return build_container(settings, mode="memory", clock=clock)


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def prov(source_type="agent_run", **kw) -> dict:
    d = {"source_type": source_type}
    d.update(kw)
    return d
