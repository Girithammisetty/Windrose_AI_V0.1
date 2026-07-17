"""Shared fixtures: RSA-signed JWTs, fake clock, memory-mode app + client,
the canonical `sales` model definition, and publish helpers."""

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

ORDERS_URN = f"wr:{TENANT_A}:dataset:dataset/018f0000-0000-7000-8000-00000000000a"
CUSTOMERS_URN = f"wr:{TENANT_A}:dataset:dataset/018f0000-0000-7000-8000-00000000000b"

ORDERS_SCHEMA = {
    "order_id": "bigint", "customer_id": "bigint", "region": "varchar",
    "order_date": "date", "order_total": "double", "discount": "double",
    "status": "varchar", "gmv_amount": "double",
}
CUSTOMERS_SCHEMA = {
    "id": "bigint", "tier": "varchar", "name": "varchar", "signup_date": "date",
}

SALES_DEFINITION = {
    "entities": [
        {"name": "orders", "dataset_urn": ORDERS_URN, "table": "bronze.t42.ds_orders",
         "primary_key": ["order_id"], "dataset_version_policy": {"policy": "latest"}},
        {"name": "customers", "dataset_urn": CUSTOMERS_URN,
         "table": "bronze.t42.ds_customers", "primary_key": ["id"],
         "dataset_version_policy": {"policy": "latest"}},
    ],
    "dimensions": [
        {"name": "region", "entity": "orders", "column": "region",
         "type": "categorical", "synonyms": ["territory"]},
        {"name": "status", "entity": "orders", "column": "status", "type": "categorical"},
        {"name": "order_month", "entity": "orders", "column": "order_date",
         "type": "time", "time_grains": ["day", "week", "month", "quarter", "year"]},
        {"name": "order_date", "entity": "orders", "column": "order_date",
         "type": "time", "time_grains": ["day", "month", "year"]},
        {"name": "customer_tier", "entity": "customers", "column": "tier",
         "type": "categorical"},
    ],
    "measures": [
        {"name": "revenue", "entity": "orders", "agg": "sum", "expr": "order_total",
         "description": "Gross order revenue", "synonyms": ["sales"]},
        {"name": "avg_order_value", "entity": "orders", "agg": "avg",
         "expr": "order_total"},
        {"name": "order_count", "entity": "orders", "agg": "count"},
        {"name": "region_count", "entity": "orders", "agg": "count_distinct",
         "expr": "region"},
        {"name": "first_status", "entity": "orders", "agg": "first", "expr": "status"},
        {"name": "completed_revenue", "entity": "orders", "agg": "sum",
         "expr": "order_total", "filters": "status = 'completed'"},
        {"name": "aov", "expr_metric": "revenue / nullif(order_count, 0)"},
        {"name": "headcount", "entity": "customers", "agg": "count"},
        {"name": "gmv", "entity": "orders", "agg": "sum", "expr": "gmv_amount",
         "deprecated": True, "successor": "revenue"},
    ],
    "join_paths": [
        {"name": "orders_customers", "from_entity": "orders", "to_entity": "customers",
         "join_type": "left", "on": [{"from_column": "customer_id", "to_column": "id"}],
         "cardinality": "many_to_one"},
    ],
}


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

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
    .public_bytes(serialization.Encoding.PEM,
                  serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)


def make_token(tenant_id: str = TENANT_A, sub: str = "user-1",
               scopes: list[str] | None = None, typ: str = "user", **extra) -> str:
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


def auth(tenant_id: str = TENANT_A, sub: str = "user-1",
         scopes: list[str] | None = None, **extra) -> dict:
    return {"Authorization": f"Bearer {make_token(tenant_id, sub=sub, scopes=scopes, **extra)}"}


def make_settings() -> Settings:
    # The unit tier pins use_real_adapters=False explicitly (the RUNTIME
    # default is True, per CONVENTIONS.md rule 1) so the in-memory doubles are
    # reachable only from tests.
    return Settings(
        jwt_public_key_pem=PUBLIC_PEM,
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        use_real_adapters=False,
    )


def seed_datasets(dataset_client, tenant_id: str = TENANT_A) -> None:
    dataset_client.register(
        tenant_id, ORDERS_URN, table="bronze.t42.ds_orders", schema=ORDERS_SCHEMA,
        primary_key=["order_id"],
        top_values={"region": ["EMEA", "AMER", "APAC"],
                    "status": ["completed", "pending", "cancelled"]},
    )
    dataset_client.register(
        tenant_id, CUSTOMERS_URN, table="bronze.t42.ds_customers",
        schema=CUSTOMERS_SCHEMA, primary_key=["id"],
        top_values={"tier": ["gold", "silver", "bronze"]},
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def container(settings, clock):
    c = build_container(settings, mode="memory", clock=clock)
    seed_datasets(c.dataset_client, TENANT_A)
    seed_datasets(c.dataset_client, TENANT_B)
    return c


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def create_model(client, *, tenant=TENANT_A, name="sales",
                       definition=None, workspace=WORKSPACE) -> dict:
    resp = await client.post(
        "/api/v1/models",
        json={"workspace_id": workspace, "name": name,
              "definition": SALES_DEFINITION if definition is None else definition},
        headers=auth(tenant),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def publish_model(client, model_id: str, *, tenant=TENANT_A,
                        version_no: int = 1) -> dict:
    resp = await client.post(
        f"/api/v1/models/{model_id}/versions/{version_no}/submit",
        headers=auth(tenant, sub="author-1"))
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/v1/models/{model_id}/versions/{version_no}/approve",
        headers=auth(tenant, sub="steward-1"))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def create_published_model(client, **kwargs) -> dict:
    model = await create_model(client, **{k: v for k, v in kwargs.items()
                                          if k in ("tenant", "name", "definition",
                                                   "workspace")})
    tenant = kwargs.get("tenant", TENANT_A)
    await publish_model(client, model["id"], tenant=tenant)
    return model
