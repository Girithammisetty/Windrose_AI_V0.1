"""Shared fixtures: RSA keypair, settings, container with fakes, HTTP client."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from app.api.auth import Principal
from app.config import Settings
from app.container import build_container
from app.main import create_app
from tests.util import AUDIENCE, ISSUER, TENANT_A, TENANT_B, bearer, make_token


@pytest.fixture(scope="session")
def rsa_keys() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


@pytest.fixture
def settings(tmp_path, rsa_keys) -> Settings:
    _, public_pem = rsa_keys
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        environment="test",
        data_dir=str(tmp_path / "data"),
        jwt_public_key_pem=public_pem.decode(),
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        inline_execution=True,
        retry_backoff_base_s=0.0,
        progress_min_interval_s=0.0,
        # scaled-down part sizes so unit tests stay fast
        min_part_size=256,
        default_part_size=1024,
        max_part_size=64 * 1024 * 1024,
    )


@pytest.fixture
async def container(settings):
    c = build_container(settings)
    await c.db.create_all()
    yield c
    await c.db.dispose()


@pytest.fixture
async def client(container):
    app = create_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://ingestion.test") as http:
        yield http


@pytest.fixture
def token_a(rsa_keys) -> str:
    return make_token(rsa_keys[0], TENANT_A, sub="user-a")


@pytest.fixture
def token_b(rsa_keys) -> str:
    return make_token(rsa_keys[0], TENANT_B, sub="user-b")


@pytest.fixture
def auth_a(token_a) -> dict[str, str]:
    return bearer(token_a)


@pytest.fixture
def auth_b(token_b) -> dict[str, str]:
    return bearer(token_b)


@pytest.fixture
def principal_a() -> Principal:
    return Principal(sub="user-a", tenant_id=TENANT_A, typ="user")


@pytest.fixture
def principal_b() -> Principal:
    return Principal(sub="user-b", tenant_id=TENANT_B, typ="user")
