"""Unit tier: tenant-isolation suite against the in-memory policy fake
(MASTER-FR-004, AC-13 unit variant) + authz matrix (MASTER-FR-071)."""

from __future__ import annotations

import pytest

from tests.conftest import TENANT_A, TENANT_B, auth, create_dataset


async def _seed(client) -> dict:
    ds = await create_dataset(client, tenant=TENANT_A, name="TenantAData")
    return ds


ENDPOINTS_ON_RESOURCE = [
    ("GET", "/api/v1/datasets/{id}", None),
    ("PATCH", "/api/v1/datasets/{id}", {"description": "x"}),
    ("DELETE", "/api/v1/datasets/{id}", None),
    ("POST", "/api/v1/datasets/{id}/restore", None),
    ("GET", "/api/v1/datasets/{id}/consumers", None),
    ("GET", "/api/v1/datasets/{id}/versions", None),
    ("GET", "/api/v1/datasets/{id}/versions/1", None),
    ("POST", "/api/v1/datasets/{id}/versions/1/profile", None),
    ("GET", "/api/v1/datasets/{id}/profile", None),
]


class TestTenantIsolation:
    @pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS_ON_RESOURCE)
    async def test_cross_tenant_is_404_everywhere(self, client, method, path, body):
        """MASTER-FR-003: tenant B touching tenant A's resource -> 404, never 403."""
        ds = await _seed(client)
        url = path.format(id=ds["id"])
        resp = await client.request(method, url, json=body, headers=auth(TENANT_B))
        assert resp.status_code == 404, f"{method} {url} -> {resp.status_code}"
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_never_leaks_even_with_crafted_filters(self, client):
        """AC-13 (unit variant): crafted filters cannot surface tenant A rows."""
        await _seed(client)
        for params in (
            "", "?q=TenantAData", "?filter[tags]=sales", "?filter[status]=draft",
            "?filter[created_by]=user-1&q=TenantAData",
        ):
            resp = await client.get(f"/api/v1/datasets{params}", headers=auth(TENANT_B))
            assert resp.status_code == 200
            assert resp.json()["data"] == [], f"leak via {params!r}"

    async def test_lineage_query_cross_tenant_404(self, client):
        ds = await _seed(client)
        resp = await client.get(
            "/api/v1/lineage", params={"urn": ds["urn"]}, headers=auth(TENANT_B)
        )
        assert resp.status_code == 404

    async def test_tenant_id_in_payload_ignored_for_authorization(self, client):
        """MASTER-FR-002: tenant in body cannot override the JWT tenant."""
        resp = await client.post(
            "/api/v1/datasets",
            json={"workspace_id": "33333333-3333-4333-8333-333333333333",
                  "name": "Sneaky", "tenant_id": TENANT_A},
            headers=auth(TENANT_B),
        )
        assert resp.status_code == 201
        listed = await client.get("/api/v1/datasets", headers=auth(TENANT_A))
        assert all(d["name"] != "Sneaky" for d in listed.json()["data"])


AUTHZ_MATRIX = [
    ("POST", "/api/v1/datasets", "dataset.dataset.create",
     {"workspace_id": "33333333-3333-4333-8333-333333333333", "name": "authz"}),
    ("GET", "/api/v1/datasets", "dataset.dataset.read", None),
    ("GET", "/api/v1/datasets/{id}", "dataset.dataset.read", None),
    ("PATCH", "/api/v1/datasets/{id}", "dataset.dataset.update", {"description": "x"}),
    ("DELETE", "/api/v1/datasets/{id}", "dataset.dataset.delete", None),
    ("POST", "/api/v1/datasets/{id}/restore", "dataset.dataset.update", None),
    ("GET", "/api/v1/datasets/{id}/consumers", "dataset.dataset.read", None),
    ("POST", "/api/v1/datasets:similar", "dataset.dataset.read", {"columns": ["a"]}),
    ("GET", "/api/v1/datasets/{id}/versions", "dataset.dataset.read", None),
    ("GET", "/api/v1/datasets/{id}/versions/1", "dataset.dataset.read", None),
    ("POST", "/api/v1/datasets/{id}/versions/1/profile", "dataset.profile.execute", None),
    ("GET", "/api/v1/datasets/{id}/profile", "dataset.profile.read", None),
    ("POST", "/api/v1/lineage/edges", "dataset.lineage.update",
     {"from_urn": f"wr:{TENANT_A}:pipeline:run/a",
      "to_urn": f"wr:{TENANT_A}:pipeline:run/b", "activity": "derived"}),
    ("GET", "/api/v1/lineage?urn=wr:" + TENANT_A + ":dataset:dataset/x",
     "dataset.lineage.read", None),
]


class TestAuthzMatrix:
    @pytest.mark.parametrize(("method", "path", "action", "body"), AUTHZ_MATRIX)
    async def test_missing_scope_403_present_scope_not_403(
        self, client, method, path, action, body
    ):
        ds = await _seed(client)
        url = path.format(id=ds["id"])
        denied = await client.request(
            method, url, json=body, headers=auth(TENANT_A, scopes=["some.other.scope"])
        )
        assert denied.status_code == 403, f"{method} {url} should deny"
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"

        allowed = await client.request(
            method, url, json=body, headers=auth(TENANT_A, scopes=[action])
        )
        assert allowed.status_code not in (401, 403), f"{method} {url} should allow"
