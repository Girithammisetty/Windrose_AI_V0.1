"""Unit: dataset CRUD, restore (AC-10), similarity (AC-11), delete-with-consumers
(AC-12), pagination, error envelope, idempotency (MASTER-FR-022/024/025)."""

from __future__ import annotations

from tests.conftest import TENANT_A, TENANT_B, WORKSPACE, auth, create_dataset


class TestCrud:
    async def test_create_and_get(self, client, container):
        ds = await create_dataset(client, name="Orders", tags=["sales"])
        assert ds["status"] == "draft"
        assert ds["urn"] == f"wr:{TENANT_A}:dataset:dataset/{ds['id']}"
        assert ds["iceberg_table"].startswith("bronze.")
        resp = await client.get(f"/api/v1/datasets/{ds['id']}", headers=auth())
        assert resp.status_code == 200
        assert resp.headers["ETag"]
        assert resp.json()["data"]["name"] == "Orders"
        assert container.memory_state.events_of_type("dataset.created")

    async def test_name_conflict_409_case_insensitive(self, client):
        await create_dataset(client, name="Orders")
        resp = await client.post(
            "/api/v1/datasets",
            json={"workspace_id": WORKSPACE, "name": "orders"},
            headers=auth(),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CONFLICT"

    async def test_patch_with_etag_and_stale_conflict(self, client):
        ds = await create_dataset(client)
        etag = ds["etag"]
        resp = await client.patch(
            f"/api/v1/datasets/{ds['id']}",
            json={"description": "hello"},
            headers={**auth(), "If-Match": etag},
        )
        assert resp.status_code == 200
        stale = await client.patch(
            f"/api/v1/datasets/{ds['id']}",
            json={"description": "again"},
            headers={**auth(), "If-Match": etag},
        )
        assert stale.status_code == 409  # BR-11

    async def test_rename_and_description_edit(self, client, container):
        """Tenant-facing edit: rename + description via PATCH (no If-Match) emits
        dataset.updated and re-indexes the new name for search."""
        ds = await create_dataset(client, name="Claims")
        resp = await client.patch(
            f"/api/v1/datasets/{ds['id']}",
            json={"name": "Auto Claims", "description": "first-party auto"},
            headers=auth(),
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["name"] == "Auto Claims"
        assert body["description"] == "first-party auto"
        assert container.memory_state.events_of_type("dataset.updated")
        # New name is searchable after re-index (old name no longer resolves).
        found = await client.get("/api/v1/datasets?q=Auto Claims", headers=auth())
        assert any(d["id"] == ds["id"] for d in found.json()["data"])

    async def test_description_only_edit_keeps_name(self, client):
        ds = await create_dataset(client, name="Payments")
        resp = await client.patch(
            f"/api/v1/datasets/{ds['id']}",
            json={"description": "ledger export"},
            headers=auth(),
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["name"] == "Payments"
        assert body["description"] == "ledger export"

    async def test_rename_to_taken_name_conflicts(self, client):
        """Renaming onto another dataset's name in the same workspace -> 409
        (uniqueness check excludes self, so re-saving the same name is fine)."""
        await create_dataset(client, name="Taken")
        ds = await create_dataset(client, name="Free")
        resp = await client.patch(
            f"/api/v1/datasets/{ds['id']}",
            json={"name": "Taken"},
            headers=auth(),
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CONFLICT"
        # Re-saving its own name is not a self-conflict.
        same = await client.patch(
            f"/api/v1/datasets/{ds['id']}",
            json={"name": "Free", "description": "unchanged name"},
            headers=auth(),
        )
        assert same.status_code == 200

    async def test_rename_cross_tenant_is_404(self, client):
        ds = await create_dataset(client, tenant=TENANT_A, name="Owned")
        resp = await client.patch(
            f"/api/v1/datasets/{ds['id']}",
            json={"name": "Hijacked"},
            headers=auth(TENANT_B),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    async def test_deprecation_surfaces_warnings(self, client, container):
        ds = await create_dataset(client)
        resp = await client.patch(
            f"/api/v1/datasets/{ds['id']}",
            json={"lifecycle": "deprecated",
                  "successor_urn": f"wr:{TENANT_A}:dataset:dataset/new"},
            headers=auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["warnings"][0]["code"] == "DATASET_DEPRECATED"
        assert container.memory_state.events_of_type("dataset.deprecated")

    async def test_error_envelope_shape(self, client):
        resp = await client.get("/api/v1/datasets/00000000-0000-0000-0000-000000000000",
                                headers=auth())
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["code"] == "NOT_FOUND"
        assert err["trace_id"]
        assert resp.headers["X-Trace-Id"]

    async def test_missing_token_401(self, client):
        resp = await client.get("/api/v1/datasets")
        assert resp.status_code == 401

    async def test_idempotency_key_replay(self, client, container):
        body = {"workspace_id": WORKSPACE, "name": "IdemDs"}
        headers = {**auth(), "Idempotency-Key": "k-123"}
        first = await client.post("/api/v1/datasets", json=body, headers=headers)
        second = await client.post("/api/v1/datasets", json=body, headers=headers)
        assert first.status_code == 201
        assert second.status_code == 201
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert first.json() == second.json()
        assert len(container.memory_state.events_of_type("dataset.created")) == 1


class TestDeleteRestore:
    async def test_delete_and_restore_within_window(self, client, container):
        ds = await create_dataset(client, name="Orders")
        resp = await client.delete(f"/api/v1/datasets/{ds['id']}", headers=auth())
        assert resp.status_code == 200
        assert (await client.get(f"/api/v1/datasets/{ds['id']}",
                                 headers=auth())).status_code == 404
        resp = await client.post(f"/api/v1/datasets/{ds['id']}/restore", headers=auth())
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Orders"
        assert container.memory_state.events_of_type("dataset.restored")

    async def test_ac10_restore_renames_on_conflict_and_410_after_window(
        self, client, clock, container
    ):
        """AC-10: restore -> `Copy of <name>` when taken; 410 after 30 days."""
        old = await create_dataset(client, name="Orders")
        await client.delete(f"/api/v1/datasets/{old['id']}", headers=auth())
        clock.advance(days=10)
        await create_dataset(client, name="Orders")  # name now taken
        resp = await client.post(f"/api/v1/datasets/{old['id']}/restore", headers=auth())
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Copy of Orders"

        gone = await create_dataset(client, name="Ancient")
        await client.delete(f"/api/v1/datasets/{gone['id']}", headers=auth())
        clock.advance(days=31)
        resp = await client.post(f"/api/v1/datasets/{gone['id']}/restore", headers=auth())
        assert resp.status_code == 410
        assert resp.json()["error"]["code"] == "GONE"

    async def test_ac12_delete_with_consumers(self, client, container):
        """AC-12: downstream consumers -> 409 without force; force soft-deletes."""
        ds = await create_dataset(client, name="Popular")
        await client.post(
            "/api/v1/lineage/edges",
            json={"from_urn": ds["urn"],
                  "to_urn": f"wr:{TENANT_A}:experiment:model/m1",
                  "activity": "trained"},
            headers=auth(),
        )
        resp = await client.delete(f"/api/v1/datasets/{ds['id']}", headers=auth())
        assert resp.status_code == 409
        assert resp.json()["error"]["details"]["downstream_edges"] == 1
        resp = await client.delete(f"/api/v1/datasets/{ds['id']}?force=true",
                                   headers=auth())
        assert resp.status_code == 200
        assert container.memory_state.events_of_type("dataset.deleted_with_consumers")

    async def test_consumers_summary(self, client):
        ds = await create_dataset(client, name="Impact")
        for i, activity in enumerate(["trained", "exported"]):
            await client.post(
                "/api/v1/lineage/edges",
                json={"from_urn": ds["urn"],
                      "to_urn": f"wr:{TENANT_A}:experiment:model/m{i}",
                      "activity": activity},
                headers=auth(),
            )
        resp = await client.get(f"/api/v1/datasets/{ds['id']}/consumers", headers=auth())
        data = resp.json()["data"]
        assert data["downstream_edges"] == 2
        assert data["by_service"] == {"experiment": 2}


class TestListSearch:
    async def test_pagination_cursor(self, client):
        for i in range(5):
            await create_dataset(client, name=f"ds-{i}")
        resp = await client.get("/api/v1/datasets?limit=2", headers=auth())
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["page"]["has_more"] is True
        seen = {d["id"] for d in body["data"]}
        cursor = body["page"]["next_cursor"]
        while cursor:
            resp = await client.get(f"/api/v1/datasets?limit=2&cursor={cursor}",
                                    headers=auth())
            body = resp.json()
            ids = {d["id"] for d in body["data"]}
            assert not ids & seen  # no overlap between pages
            seen |= ids
            cursor = body["page"]["next_cursor"]
        assert len(seen) == 5

    async def test_filters_and_q(self, client):
        await create_dataset(client, name="Sales Orders", tags=["sales", "pii:email"],
                             description="orders fact table")
        await create_dataset(client, name="Inventory", tags=["ops"])
        resp = await client.get("/api/v1/datasets?filter[tags]=sales", headers=auth())
        assert [d["name"] for d in resp.json()["data"]] == ["Sales Orders"]
        resp = await client.get("/api/v1/datasets?q=orders", headers=auth())
        assert [d["name"] for d in resp.json()["data"]] == ["Sales Orders"]
        resp = await client.get("/api/v1/datasets?q=zzz-nothing", headers=auth())
        assert resp.json()["data"] == []

    async def test_sort_by_name(self, client):
        for name in ["bravo", "alpha", "charlie"]:
            await create_dataset(client, name=name)
        resp = await client.get("/api/v1/datasets?sort=name", headers=auth())
        assert [d["name"] for d in resp.json()["data"]] == ["alpha", "bravo", "charlie"]


class TestSimilarity:
    async def test_ac11_column_overlap_ranking(self, client, container):
        """AC-11: datasets containing both columns rank above partial matches."""
        state = container.memory_state
        both = await create_dataset(client, name="BothCols")
        partial = await create_dataset(client, name="OneCol")
        neither = await create_dataset(client, name="NoCols")

        from datetime import UTC, datetime

        from app.domain.entities import DatasetVersion

        def add_version(ds, schema, vid):
            v = DatasetVersion(
                id=vid, tenant_id=TENANT_A, dataset_id=ds["id"], version_no=1,
                iceberg_snapshot_id=1, schema=schema,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            state.versions[vid] = v
            state.datasets[ds["id"]].current_version_id = vid

        add_version(both, {"Customer_ID": {"type": "long"},
                           "order_total": {"type": "double"}}, "v-both")
        add_version(partial, {"customer_id": {"type": "long"}}, "v-partial")
        add_version(neither, {"sku": {"type": "string"}}, "v-none")

        resp = await client.post(
            "/api/v1/datasets:similar",
            json={"columns": ["customer_id", "ORDER_TOTAL"]},
            headers=auth(),
        )
        assert resp.status_code == 200
        ranked = resp.json()["data"]
        assert [r["name"] for r in ranked] == ["BothCols", "OneCol"]
        assert ranked[0]["matched_columns"] == ["customer_id", "order_total"]

    async def test_similar_requires_schema_or_columns(self, client):
        resp = await client.post("/api/v1/datasets:similar", json={}, headers=auth())
        assert resp.status_code == 422
