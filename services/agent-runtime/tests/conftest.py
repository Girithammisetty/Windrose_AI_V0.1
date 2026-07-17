"""Shared test fixtures. Unit tier uses in-memory doubles (use_real_adapters=False)."""

from __future__ import annotations

import time
import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


TEST_PRIV, TEST_PUB = _keypair()


def make_settings(**over) -> Settings:
    base = dict(
        use_real_adapters=False,
        use_temporal=False,
        store_mode="memory",
        jwt_public_key_pem=TEST_PUB,
        jwt_issuer="https://identity.windrose.local",
        jwt_audience="windrose",
    )
    base.update(over)
    return Settings(**base)


def make_token(*, sub: str, tenant_id: str, typ: str = "user", scopes=None,
               obo_sub: str | None = None) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://identity.windrose.local", "aud": "windrose", "sub": sub,
        "tenant_id": tenant_id, "typ": typ, "scopes": scopes or [],
        "iat": now, "exp": now + 3600,
    }
    if obo_sub:
        claims["obo_sub"] = obo_sub
    return pyjwt.encode(claims, TEST_PRIV, algorithm="RS256")


@pytest.fixture
def settings():
    return make_settings()
