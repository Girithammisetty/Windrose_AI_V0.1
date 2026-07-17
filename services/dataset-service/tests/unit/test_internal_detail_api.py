"""Unit: internal dataset-detail endpoints semantic-service calls for binding
validation (SEM-FR-002 / B3):

- GET /internal/v1/datasets/{id}          -> {physical_table, schema, primary_key}
- GET /internal/v1/datasets/{id}/profile  -> {schema, top_values}

Guarded by require_internal (SPIFFE allowlist incl. semantic-service) with the
tenant supplied via the x-windrose-tenant-id header.
"""

from __future__ import annotations

import pandas as pd

from tests.conftest import SPIFFE_INGESTION, TENANT_A, TENANT_B, create_dataset

SPIFFE_SEMANTIC = "spiffe://windrose/ns/data/sa/semantic-service"

DF = pd.DataFrame(
    {
        "claim_id": [f"C{i}" for i in range(14)],
        "claim_type": ["auto"] * 14,
        "vendor": ["acme"] * 14,
        "amount": [str(100 + i) for i in range(14)],
    }
)


async def _register_version(
    client, container, ds, snapshot_id=2001, df=DF, schema=None, skip_profiling=True
):
    await container.catalog.commit_snapshot(ds["iceberg_table"], snapshot_id, df)
    resp = await client.post(
        f"/internal/v1/datasets/{ds['id']}/versions",
        json={
            "tenant_id": TENANT_A,
            "iceberg_snapshot_id": snapshot_id,
            "schema": schema if schema is not None else {},
            "row_count": len(df),
            "bytes": 4044,
            "skip_profiling": skip_profiling,
        },
        headers={"x-client-spiffe-id": SPIFFE_INGESTION},
    )
    assert resp.status_code == 201, resp.text
    return resp


def _sem_headers(tenant=TENANT_A):
    return {"x-client-spiffe-id": SPIFFE_SEMANTIC, "x-windrose-tenant-id": tenant}


class TestInternalDetail:
    async def test_detail_returns_physical_table_and_schema(self, client, container):
        ds = await create_dataset(client, name="auto-claims-1783755028")
        await _register_version(client, container, ds)

        resp = await client.get(
            f"/internal/v1/datasets/{ds['id']}", headers=_sem_headers()
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # physical_table = main.<normalized relation> (matches entity.table).
        assert data["physical_table"] == "main.auto_claims_1783755028"
        assert data["primary_key"] == []
        # Empty version schema -> columns fall back to the physical Iceberg table.
        assert {"claim_type", "vendor", "amount"} <= set(data["schema"].keys())

    async def test_detail_uses_version_schema_types(self, client, container):
        ds = await create_dataset(client, name="Orders")
        await _register_version(
            client, container, ds,
            schema={"order_id": {"type": "long", "nullable": False, "tags": []},
                    "email": {"type": "string", "nullable": True, "tags": ["pii:email"]}},
        )
        resp = await client.get(
            f"/internal/v1/datasets/{ds['id']}", headers=_sem_headers()
        )
        assert resp.status_code == 200, resp.text
        schema = resp.json()["data"]["schema"]
        assert schema == {"order_id": "long", "email": "string"}

    async def test_profile_returns_schema_dict(self, client, container):
        """skip_profiling=True: no profile exists yet -> top_values legitimately {}."""
        ds = await create_dataset(client, name="auto-claims-1783755028")
        await _register_version(client, container, ds)
        resp = await client.get(
            f"/internal/v1/datasets/{ds['id']}/profile", headers=_sem_headers()
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert {"claim_type", "vendor", "amount"} <= set(data["schema"].keys())
        assert data["top_values"] == {}

    async def test_profile_projects_real_top_values_once_profiled(self, client, container):
        """SEM-FR-002/080: after a REAL profile completes, top_values projects the
        per-column sample values from profile.json ({col: [most-frequent, ...]})
        for semantic-service's sample-value validation — not a hardcoded {}."""
        ds = await create_dataset(client, name="profiled-claims")
        # skip_profiling=False -> the in-process profiler runs and completes via
        # the signed callback, persisting profile.json to the object store.
        await _register_version(client, container, ds, skip_profiling=False)

        resp = await client.get(
            f"/internal/v1/datasets/{ds['id']}/profile", headers=_sem_headers()
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        top = data["top_values"]
        assert top, "top_values must be projected from the completed profile"
        # categorical string columns carry their real most-frequent values
        assert top["claim_type"] == ["auto"]
        assert top["vendor"] == ["acme"]
        # every unique claim_id appears (14 rows < MAX_TOP_VALUES=20)
        assert set(top["claim_id"]) == {f"C{i}" for i in range(14)}
        # values are raw strings (semantic-service slices sample_values[:10])
        assert all(isinstance(v, str) for vals in top.values() for v in vals)

    async def test_profile_top_values_tenant_scoped(self, client, container):
        ds = await create_dataset(client, name="profiled-claims-2")
        await _register_version(client, container, ds, skip_profiling=False)
        resp = await client.get(
            f"/internal/v1/datasets/{ds['id']}/profile", headers=_sem_headers(TENANT_B)
        )
        assert resp.status_code == 404

    async def test_detail_requires_allowed_spiffe(self, client, container):
        ds = await create_dataset(client, name="Orders")
        await _register_version(client, container, ds)
        resp = await client.get(
            f"/internal/v1/datasets/{ds['id']}",
            headers={"x-client-spiffe-id": "spiffe://windrose/ns/x/sa/rogue",
                     "x-windrose-tenant-id": TENANT_A},
        )
        assert resp.status_code == 403

    async def test_detail_requires_tenant_header(self, client, container):
        ds = await create_dataset(client, name="Orders")
        await _register_version(client, container, ds)
        resp = await client.get(
            f"/internal/v1/datasets/{ds['id']}",
            headers={"x-client-spiffe-id": SPIFFE_SEMANTIC},
        )
        assert resp.status_code == 422

    async def test_detail_is_tenant_scoped(self, client, container):
        ds = await create_dataset(client, name="Orders")
        await _register_version(client, container, ds)
        # Correct dataset id but a foreign tenant header -> 404.
        resp = await client.get(
            f"/internal/v1/datasets/{ds['id']}", headers=_sem_headers(TENANT_B)
        )
        assert resp.status_code == 404
