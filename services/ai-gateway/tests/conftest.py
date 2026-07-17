"""Shared fixtures: RSA-signed JWTs, fake clock, memory-mode container/app,
deployment + key seeding helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.container import build_container
from app.domain.windows import window_start
from app.main import create_app

ISSUER = "https://identity.windrose.local"
AUDIENCE = "windrose"
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"
SPIFFE_AGENT_RUNTIME = "spiffe://windrose/ns/ai/sa/agent-runtime"


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


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
    cell_cloud: str | None = "aws",
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
    }
    if cell_cloud is not None:
        claims["cell_cloud"] = cell_cloud
    claims.update(extra)
    return pyjwt.encode(claims, PRIVATE_PEM, algorithm="RS256")


def admin_auth(tenant_id: str = TENANT_A, scopes: list[str] | None = None,
               **extra) -> dict:
    return {"Authorization": f"Bearer {make_token(tenant_id, scopes=scopes, **extra)}"}


def dp_headers(secret: str, tenant_id: str = TENANT_A, *, request_class: str | None = None,
               token: str | None = None, **extra_headers) -> dict:
    headers = {
        "Authorization": f"Bearer {secret}",
        "X-Windrose-JWT": token or make_token(tenant_id),
    }
    if request_class:
        headers["x-windrose-request-class"] = request_class
    headers.update(extra_headers)
    return headers


def make_settings(**overrides) -> Settings:
    # The unit tier pins use_real_adapters=False explicitly (the RUNTIME
    # default is True, per CONVENTIONS.md rule 1) so the in-memory doubles are
    # reachable only from tests.
    overrides.setdefault("use_real_adapters", False)
    return Settings(
        jwt_public_key_pem=PUBLIC_PEM,
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        **overrides,
    )


async def _noop_sleeper(ms: int) -> None:
    return None


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def container(settings, clock):
    return build_container(settings, mode="memory", clock=clock,
                           sleeper=_noop_sleeper)


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------- seeding


async def seed_deployment(container, *, alias: str = "fast-small",
                          cloud: str = "aws", priority: int = 10,
                          provider: str = "bedrock",
                          name: str | None = None, status: str = "active"):
    d = await container.provider_admin.create({
        "provider": provider,
        "model_family": alias,
        "deployment_name": name or f"{provider}-{alias}-{cloud}-{priority}",
        "region": "us-east-1",
        "cloud": cloud,
        "endpoint_vault_ref": f"secret/ai/{provider}/{alias}",
        "priority": priority,
    })
    if status != "active":
        d = await container.provider_admin.patch(d.id, {"status": status},
                                                 force=True)
    return d


async def seed_default_deployments(container):
    """One aws deployment per default ladder alias."""
    out = {}
    for alias in ("fast-small", "balanced", "frontier", "embed-standard"):
        out[alias] = await seed_deployment(container, alias=alias)
    return out


async def mint_key(container, tenant_id: str = TENANT_A, *,
                   principal_type: str = "user", principal_id: str = "user-1",
                   classes: list[str] | None = None, max_rung: int = 2):
    key, secret = await container.key_service.create(
        tenant_id, principal_type=principal_type, principal_id=principal_id,
        allowed_request_classes=classes, max_rung=max_rung,
    )
    return key, secret


def ledger_key_for(budget_id: str, window: str, clock, tz: str = "UTC") -> str:
    return f"bud:{budget_id}:{window_start(window, clock.now(), tz)}"


CHAT_BODY = {
    "model": "windrose-auto",
    "messages": [{"role": "user", "content": "revenue by region, Q3"}],
}
