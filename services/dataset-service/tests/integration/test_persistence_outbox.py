"""Integration: persistence round-trips, transactional outbox + dispatcher
(MASTER-FR-034), full profile pipeline against Postgres (AC-2 storage side)."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from sqlalchemy import text

from app.store.sql import OutboxDispatcher
from tests.conftest import SPIFFE_INGESTION, TENANT_A, auth, create_dataset

pytestmark = pytest.mark.integration

DF = pd.DataFrame({"order_id": range(50), "order_total": [1.5] * 50})


async def register_version(client, container, ds, snapshot_id=1001, **body):
    await container.catalog.commit_snapshot(ds["iceberg_table"], snapshot_id, DF)
    return await client.post(
        f"/internal/v1/datasets/{ds['id']}/versions",
        json={"tenant_id": TENANT_A, "iceberg_snapshot_id": snapshot_id,
              "schema": {"order_id": {"type": "long"},
                         "order_total": {"type": "double"}},
              "row_count": 50, **body},
        headers={"x-client-spiffe-id": SPIFFE_INGESTION},
    )


class TestPersistence:
    async def test_dataset_crud_roundtrip(self, client):
        ds = await create_dataset(client, name="Persisted", tags=["gold"])
        resp = await client.get(f"/api/v1/datasets/{ds['id']}", headers=auth())
        data = resp.json()["data"]
        assert data["name"] == "Persisted"
        assert data["tags"] == ["gold"]
        patched = await client.patch(
            f"/api/v1/datasets/{ds['id']}", json={"description": "d"}, headers=auth()
        )
        assert patched.json()["data"]["description"] == "d"

    async def test_full_profile_pipeline_persists(self, client, container, engine):
        """AC-2 (storage side): pointer + <=64KB summary in Postgres, blobs on disk."""
        ds = await create_dataset(client, name="Profiled")
        resp = await register_version(client, container, ds)
        assert resp.status_code == 201, resp.text

        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A}
            )
            row = (await conn.execute(text(
                "SELECT status, object_key_json, summary, "
                "pg_column_size(summary) AS sz FROM profiles"
            ))).mappings().one()
        assert row["status"] == "completed"
        assert row["object_key_json"].startswith(f"profiles/{TENANT_A}/")
        assert row["sz"] <= 64 * 1024  # BR-4 no-blob rule enforced by CHECK too
        summary = row["summary"] if isinstance(row["summary"], dict) else json.loads(
            row["summary"]
        )
        assert {c["name"] for c in summary["columns"]} == {"order_id", "order_total"}
        assert await container.object_store.exists(row["object_key_json"])

        got = await client.get(f"/api/v1/datasets/{ds['id']}", headers=auth())
        assert got.json()["data"]["status"] == "ready"
        assert got.json()["data"]["current_version"]["profile_status"] == "completed"

    async def test_version_pagination_and_get(self, client, container):
        ds = await create_dataset(client, name="ManyVersions")
        for i in range(1, 4):
            resp = await register_version(client, container, ds, snapshot_id=1000 + i,
                                          skip_profiling=True)
            assert resp.status_code == 201
        resp = await client.get(
            f"/api/v1/datasets/{ds['id']}/versions?limit=2", headers=auth()
        )
        body = resp.json()
        assert [v["version_no"] for v in body["data"]] == [3, 2]
        assert body["page"]["has_more"] is True
        resp = await client.get(
            f"/api/v1/datasets/{ds['id']}/versions?limit=2"
            f"&cursor={body['page']['next_cursor']}",
            headers=auth(),
        )
        assert [v["version_no"] for v in resp.json()["data"]] == [1]


class TestOutbox:
    async def test_outbox_written_in_transaction_then_dispatched(
        self, client, container, engine
    ):
        """MASTER-FR-034: event rows commit with the mutation; the dispatcher
        publishes them to the bus exactly once."""
        await create_dataset(client, name="Outboxed")

        async with engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.worker', 'true', true)"))
            rows = (await conn.execute(text(
                "SELECT event_type, published_at FROM outbox ORDER BY created_at"
            ))).all()
        assert ("dataset.created", None) in [(r[0], r[1]) for r in rows]

        dispatcher = OutboxDispatcher(container.extras["session_factory"], container.bus)
        published = await dispatcher.run_once()
        assert published >= 1
        assert [e["event_type"] for _, e in container.bus.published].count(
            "dataset.created"
        ) == 1
        assert await dispatcher.run_once() == 0  # nothing left / no double publish

    async def test_failed_request_leaves_no_outbox_rows(self, client, engine):
        """Rollback discards both the row and its event (atomicity)."""
        await create_dataset(client, name="Taken")
        resp = await client.post(
            "/api/v1/datasets",
            json={"workspace_id": "33333333-3333-4333-8333-333333333333",
                  "name": "Taken"},
            headers=auth(),
        )
        assert resp.status_code == 409
        async with engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.worker', 'true', true)"))
            count = (await conn.execute(text(
                "SELECT count(*) FROM outbox WHERE event_type = 'dataset.created'"
            ))).scalar()
        assert count == 1  # only the first create emitted

    async def test_idempotency_key_persisted_replay(self, client, engine):
        headers = {**auth(), "Idempotency-Key": "int-key-1"}
        body = {"workspace_id": "33333333-3333-4333-8333-333333333333", "name": "IdemInt"}
        first = await client.post("/api/v1/datasets", json=body, headers=headers)
        second = await client.post("/api/v1/datasets", json=body, headers=headers)
        assert first.status_code == 201
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert first.json()["data"]["id"] == second.json()["data"]["id"]
