"""Ingestion job API behaviour (ING-FR-020..028, ING-FR-082, BR-7)."""

from __future__ import annotations

import sqlalchemy as sa

from app.domain.querysource import FakeQuerySource
from app.ids import uuid7
from app.store.models import Ingestion
from tests.util import TENANT_A, create_connection

ROWS = [
    {"id": i, "name": f"n{i}", "updated_at": f"2026-07-0{1 + i % 5}T00:00:00+00:00"}
    for i in range(7)
]


async def test_query_ingestion_runs_to_completed(client, auth_a, container) -> None:
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT * FROM public.orders",
            "new_dataset": {"name": "Orders"},
        },
        headers=auth_a,
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()["data"]
    assert job["operation_id"] == job["id"]
    assert job["status"] == "completed"
    assert job["rows_appended"] == len(ROWS)
    assert job["file_format"] == "parquet"  # BR-2
    assert job["iceberg_snapshot_id"] is not None
    snapshots = container.table_writer.all_snapshots()
    assert len(snapshots) == 1 and snapshots[0]["summary"]["ingestion_id"] == job["id"]


async def test_target_xor_validation(client, auth_a) -> None:
    conn_less = {"ingestion_mode": "file_upload", "file_format": "csv"}
    resp = await client.post("/api/v1/ingestions", json=conn_less, headers=auth_a)
    assert resp.status_code == 422
    both = {
        **conn_less,
        "dataset_urn": f"wr:{TENANT_A}:dataset:dataset/{uuid7()}",
        "new_dataset": {"name": "x"},
    }
    resp = await client.post("/api/v1/ingestions", json=both, headers=auth_a)
    assert resp.status_code == 422


async def test_query_mode_requires_statement_and_connection(client, auth_a) -> None:
    resp = await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "query", "new_dataset": {"name": "x"}},
        headers=auth_a,
    )
    assert resp.status_code == 422
    fields = {d["field"] for d in resp.json()["error"]["details"]}
    assert {"statement", "connection_id"} <= fields


async def test_scheduled_run_cannot_be_created_via_api(client, auth_a) -> None:
    resp = await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "scheduled_run", "new_dataset": {"name": "x"}},
        headers=auth_a,
    )
    assert resp.status_code == 422


async def test_unknown_connection_404(client, auth_a) -> None:
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": uuid7(),
            "statement": "SELECT 1",
            "new_dataset": {"name": "x"},
        },
        headers=auth_a,
    )
    assert resp.status_code == 404


async def test_file_format_mandatory_for_file_upload(client, auth_a) -> None:
    resp = await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "file_upload", "new_dataset": {"name": "x"}},
        headers=auth_a,
    )
    assert resp.status_code == 422
    assert any(d["field"] == "file_format" for d in resp.json()["error"]["details"])


async def test_list_filters_by_status(client, auth_a, container) -> None:
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth_a)
    await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT 1",
            "new_dataset": {"name": "a"},
        },
        headers=auth_a,
    )
    await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "file_upload", "file_format": "csv", "new_dataset": {"name": "b"}},
        headers=auth_a,
    )
    resp = await client.get(
        "/api/v1/ingestions", params={"filter[status]": "completed"}, headers=auth_a
    )
    assert [j["status"] for j in resp.json()["data"]] == ["completed"]
    resp = await client.get(
        "/api/v1/ingestions", params={"filter[ingestion_mode]": "file_upload"}, headers=auth_a
    )
    assert [j["ingestion_mode"] for j in resp.json()["data"]] == ["file_upload"]


async def test_cancel_uncommitted_job_and_illegal_second_cancel(client, auth_a) -> None:
    resp = await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "file_upload", "file_format": "csv", "new_dataset": {"name": "c"}},
        headers=auth_a,
    )
    job = resp.json()["data"]
    resp = await client.post(f"/api/v1/ingestions/{job['id']}/cancel", headers=auth_a)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"
    resp = await client.post(f"/api/v1/ingestions/{job['id']}/cancel", headers=auth_a)
    assert resp.status_code == 409
    details = resp.json()["error"]["details"]
    assert details == {"current_status": "cancelled", "requested": "cancelled"}


async def test_retry_requires_failed_status(client, auth_a) -> None:
    resp = await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "file_upload", "file_format": "csv", "new_dataset": {"name": "d"}},
        headers=auth_a,
    )
    job = resp.json()["data"]
    resp = await client.post(f"/api/v1/ingestions/{job['id']}/retry", headers=auth_a)
    assert resp.status_code == 409


async def test_reingest_clones_terminal_job(client, auth_a, container) -> None:
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT 1",
            "new_dataset": {"name": "e"},
        },
        headers=auth_a,
    )
    original = resp.json()["data"]
    resp = await client.post(f"/api/v1/ingestions/{original['id']}/reingest", headers=auth_a)
    assert resp.status_code == 202
    clone = resp.json()["data"]
    assert clone["id"] != original["id"]
    assert clone["retried_from_id"] == original["id"]
    assert clone["status"] == "completed"
    assert clone["dataset_urn"] == original["dataset_urn"]


async def test_webhook_batch_creation_gated_501(client, auth_a) -> None:
    """HONEST GATE: the webhook buffer->Iceberg flush is unimplemented, so
    webhook_batch ingestions are rejected 501 at create time — accepted events
    would otherwise buffer forever and never become dataset rows (ING-FR-024).
    The signing-secret/HMAC/dedup machinery stays covered in
    tests/acceptance/test_acceptance.py::test_ac11_webhook_hmac_and_event_id_dedup
    via directly-seeded endpoint rows."""
    resp = await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "webhook_batch", "new_dataset": {"name": "hooks"}},
        headers=auth_a,
    )
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "NOT_IMPLEMENTED"
    assert "flush to Iceberg" in resp.json()["error"]["message"]


async def _insert_running_jobs(
    container, tenant_id: str, count: int, dataset_urn: str | None = None
):
    async with container.db.tenant_session(tenant_id) as session:
        for _i in range(count):
            session.add(
                Ingestion(
                    id=uuid7(),
                    tenant_id=tenant_id,
                    workspace_id="00000000-0000-0000-0000-000000000000",
                    ingestion_mode="query",
                    dataset_urn=dataset_urn or f"wr:{tenant_id}:dataset:dataset/{uuid7()}",
                    status="running",
                )
            )
        await session.commit()


async def test_tenant_concurrency_cap_queues_excess(client, auth_a, container) -> None:
    """ING-FR-082: max 5 running per tenant; excess stays queued."""
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth_a)
    await _insert_running_jobs(container, TENANT_A, container.settings.max_running_per_tenant)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT 1",
            "new_dataset": {"name": "capped"},
        },
        headers=auth_a,
    )
    assert resp.status_code == 202
    assert resp.json()["data"]["status"] == "queued"


async def test_br7_single_running_job_per_dataset(client, auth_a, container) -> None:
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth_a)
    dataset_urn = f"wr:{TENANT_A}:dataset:dataset/{uuid7()}"
    await _insert_running_jobs(container, TENANT_A, 1, dataset_urn=dataset_urn)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT 1",
            "dataset_urn": dataset_urn,
        },
        headers=auth_a,
    )
    assert resp.status_code == 202
    assert resp.json()["data"]["status"] == "queued"  # BR-7: waits for the running job


async def test_transitions_recorded(client, auth_a, container) -> None:
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT 1",
            "new_dataset": {"name": "t"},
        },
        headers=auth_a,
    )
    job = resp.json()["data"]
    from app.store.models import IngestionTransition

    async with container.db.tenant_session(TENANT_A) as session:
        rows = (
            (
                await session.execute(
                    sa.select(IngestionTransition)
                    .where(IngestionTransition.ingestion_id == job["id"])
                    .order_by(IngestionTransition.created_at, IngestionTransition.id)
                )
            )
            .scalars()
            .all()
        )
    path = [(t.from_status, t.to_status) for t in rows]
    assert path == [
        ("created", "queued"),
        ("queued", "running"),
        ("running", "committing"),
        ("committing", "completed"),
    ]
