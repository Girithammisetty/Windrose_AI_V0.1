"""Shared fixtures: RSA-signed JWTs, fake clock, memory-mode container/app/client."""

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


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


class FakeJudgeClient:
    """Unit-tier judge double (used ONLY in tests). Returns a deterministic rating
    so scorer/gate logic can be exercised without a network. NEVER wired at runtime."""

    def __init__(
        self, rating: float = 4.0, rationale: str = "looks supported", cost_usd: float = 0.0
    ):
        self.calls = 0
        self._rating = rating
        self._rationale = rationale
        self._cost = cost_usd

    async def judge(self, *, messages, tenant_id, max_tokens=256):
        from app.adapters.judge_client import JudgeResult

        self.calls += 1
        import json

        content = json.dumps({"rating": self._rating, "rationale": self._rationale})
        return JudgeResult(
            content=content,
            input_tokens=20,
            output_tokens=8,
            model="fake-judge",
            cost_usd=self._cost,
            latency_ms=1,
            trace_ref="fake-trace",
        )


_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
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


def make_settings(tmp_path=None, **overrides) -> Settings:
    # Tests explicitly opt into the unit/dev-tier doubles (real adapters are the
    # runtime DEFAULT). Individual tests override use_real_adapters where they
    # exercise a real adapter directly.
    kw = dict(jwt_public_key_pem=PUBLIC_PEM, jwt_issuer=ISSUER, jwt_audience=AUDIENCE,
              use_real_adapters=False)
    if tmp_path is not None:
        kw["fixture_warehouse_dir"] = str(tmp_path / "fixtures")
    kw.update(overrides)
    return Settings(**kw)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def judge():
    return FakeJudgeClient()


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def container(settings, clock, judge):
    return build_container(settings, mode="memory", clock=clock, judge_client=judge)


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---- helpers used across tests ----


def sql_case(case_id_tag: str, nl: str, sql: str, *, tolerance=0.01) -> dict:
    return {
        "dataset_key": "analytics/nl2sql",
        "agent_key": "analytics",
        "input": {
            "messages": [{"role": "user", "content": nl}],
            "context_refs": {"fixture_warehouse": "fw-test"},
        },
        "expected": {
            "kind": "sql_result",
            "value": {"sql": sql, "float_tolerance": tolerance, "order_insensitive": True},
        },
        "source": "manual",
        "tags": [case_id_tag],
        "status": "active",
    }
