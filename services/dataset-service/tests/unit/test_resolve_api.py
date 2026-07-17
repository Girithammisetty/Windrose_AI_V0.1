"""Unit: internal /api/v1/datasets/resolve — the physical resolver
query-service calls (QRY-FR-005). Unauthenticated (no bearer token), tenant via
query param, returns Meta fields + physical parquet source."""

from __future__ import annotations

import pandas as pd

from tests.conftest import SPIFFE_INGESTION, TENANT_A, TENANT_B, create_dataset

DF = pd.DataFrame(
    {
        "claim_id": [f"C{i}" for i in range(14)],
        "claim_type": ["auto"] * 14,
        "vendor": ["acme"] * 14,
        "amount": [str(100 + i) for i in range(14)],
    }
)


async def _register_version(client, container, ds, snapshot_id=1001, df=DF, schema=None):
    await container.catalog.commit_snapshot(ds["iceberg_table"], snapshot_id, df)
    resp = await client.post(
        f"/internal/v1/datasets/{ds['id']}/versions",
        json={
            "tenant_id": TENANT_A,
            "iceberg_snapshot_id": snapshot_id,
            "schema": schema if schema is not None else {},
            "row_count": len(df),
            "bytes": 4044,
            "produced_by_urn": f"wr:{TENANT_A}:ingestion:ingestion/i-{snapshot_id}",
            "skip_profiling": True,
        },
        headers={"x-client-spiffe-id": SPIFFE_INGESTION},
    )
    assert resp.status_code == 201, resp.text
    return resp


class TestResolve:
    async def test_resolve_returns_physical_source_no_auth(self, client, container):
        ds = await create_dataset(client, name="auto-claims-1783755028")
        await _register_version(client, container, ds)

        # NO Authorization header — exactly how query-service calls it.
        resp = await client.get(
            "/api/v1/datasets/resolve",
            params={"name": "auto-claims-1783755028", "tenant": TENANT_A, "version": 1},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "auto-claims-1783755028"
        assert body["version"] == 1
        assert body["urn"] == f"wr:{TENANT_A}:dataset:dataset/{ds['id']}"
        assert body["namespace"] == "main"
        # physical_ident = "main"."<name normalized>" (each part double-quoted)
        assert body["physical_ident"] == '"main"."auto_claims_1783755028"'
        assert body["row_count"] == 14
        assert body["size_bytes"] == 4044
        assert body["source_format"] == "parquet"
        assert body["deprecated"] is False
        assert body["source_uris"] and body["source_uris"][0].endswith(".parquet")
        # schema was empty -> columns fall back to the physical table schema
        col_names = {c["name"] for c in body["columns"]}
        assert {"claim_type", "vendor", "amount"} <= col_names

    async def test_resolve_uses_version_schema_when_present(self, client, container):
        ds = await create_dataset(client, name="Orders")
        await _register_version(
            client, container, ds,
            schema={"order_id": {"type": "long", "nullable": False, "tags": []},
                    "email": {"type": "string", "nullable": True, "tags": ["pii:email"]}},
        )
        resp = await client.get(
            "/api/v1/datasets/resolve", params={"name": "Orders", "tenant": TENANT_A}
        )
        assert resp.status_code == 200, resp.text
        cols = {c["name"]: c for c in resp.json()["columns"]}
        assert cols["order_id"]["type"] == "long"
        assert cols["email"]["pii_tag"] == "pii:email"

    async def test_resolve_missing_tenant_returns_422(self, client):
        resp = await client.get("/api/v1/datasets/resolve", params={"name": "x"})
        assert resp.status_code == 422

    async def test_resolve_unknown_dataset_404(self, client):
        resp = await client.get(
            "/api/v1/datasets/resolve", params={"name": "nope", "tenant": TENANT_A}
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    async def test_resolve_matches_normalized_relation(self, client, container):
        # B2: real dataset name has hyphens, but query-service auto-materialize
        # asks for the NORMALIZED relation the semantic compiler emitted.
        ds = await create_dataset(client, name="auto-claims-1783755028")
        await _register_version(client, container, ds)

        resp = await client.get(
            "/api/v1/datasets/resolve",
            params={"name": "auto_claims_1783755028", "tenant": TENANT_A, "version": 1},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Resolves to the SAME dataset as the hyphenated exact name.
        assert body["urn"] == f"wr:{TENANT_A}:dataset:dataset/{ds['id']}"
        assert body["name"] == "auto-claims-1783755028"
        assert body["physical_ident"] == '"main"."auto_claims_1783755028"'

    async def test_resolve_normalized_is_tenant_scoped(self, client, container):
        # B2 fallback must not leak across tenants.
        ds = await create_dataset(client, name="auto-claims-9")
        await _register_version(client, container, ds)
        resp = await client.get(
            "/api/v1/datasets/resolve",
            params={"name": "auto_claims_9", "tenant": TENANT_B},
        )
        assert resp.status_code == 404

    async def test_resolve_is_tenant_scoped(self, client, container):
        ds = await create_dataset(client, name="auto-claims-1")
        await _register_version(client, container, ds)
        # Same name, other tenant -> RLS/tenant scoping hides it (404).
        resp = await client.get(
            "/api/v1/datasets/resolve",
            params={"name": "auto-claims-1", "tenant": TENANT_B},
        )
        assert resp.status_code == 404
