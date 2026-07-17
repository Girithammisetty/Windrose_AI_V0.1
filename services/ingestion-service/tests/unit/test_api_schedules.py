"""Schedule API behaviour (ING-FR-060..063, BR-10)."""

from __future__ import annotations

from app.domain.querysource import FakeQuerySource
from app.ids import uuid7
from app.store.models import Ingestion
from tests.util import TENANT_A, create_connection, outbox_events

ROWS = [
    {"id": 1, "updated_at": "2026-07-02T00:00:00+00:00"},
    {"id": 2, "updated_at": "2026-07-04T00:00:00+00:00"},
    {"id": 3, "updated_at": "2026-07-06T00:00:00+00:00"},
]


def schedule_payload(connection_id: str, **overrides):
    return {
        "connection_id": connection_id,
        "cron": "0 2 * * *",
        "timezone": "Europe/Berlin",
        "ingestion_template": {
            "ingestion_mode": "query",
            "statement": "SELECT * FROM public.orders",
            "new_dataset": {"name": "orders-daily"},
        },
        "overlap_policy": "skip",
        "enabled": True,
        **overrides,
    }


async def make_schedule(client, auth, container, **overrides):
    container.query_sources.set("postgres", FakeQuerySource(rows=ROWS))
    conn = await create_connection(client, auth)
    resp = await client.post(
        "/api/v1/schedules", json=schedule_payload(conn["id"], **overrides), headers=auth
    )
    assert resp.status_code == 201, resp.text
    return conn, resp.json()["data"]


async def test_create_schedule_returns_next_fire(client, auth_a, container) -> None:
    _conn, sched = await make_schedule(client, auth_a, container)
    assert sched["temporal_schedule_id"].startswith("inproc-")
    assert sched["next_fire_at"] is not None
    assert sched["cron"] == "0 2 * * *"


async def test_invalid_cron_timezone_and_timing_rejected(client, auth_a, container) -> None:
    conn = await create_connection(client, auth_a)
    for bad in (
        {"cron": "not a cron"},
        {"timezone": "Mars/Olympus"},
        {"cron": None},  # neither cron nor interval
        {"interval_seconds": 3600},  # both cron and interval
    ):
        resp = await client.post(
            "/api/v1/schedules", json=schedule_payload(conn["id"], **bad), headers=auth_a
        )
        assert resp.status_code == 422, bad


async def test_file_poll_template_is_todo(client, auth_a, container) -> None:
    conn = await create_connection(client, auth_a)
    payload = schedule_payload(conn["id"])
    payload["ingestion_template"]["ingestion_mode"] = "file_poll"
    resp = await client.post("/api/v1/schedules", json=payload, headers=auth_a)
    assert resp.status_code == 422
    assert "TODO" in resp.json()["error"]["message"]


async def test_run_now_creates_and_completes_job(client, auth_a, container) -> None:
    _conn, sched = await make_schedule(client, auth_a, container)
    resp = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    assert resp.status_code == 200, resp.text
    fired = resp.json()["data"]
    assert fired["skipped"] is False and fired["status"] == "completed"
    job = await client.get(f"/api/v1/ingestions/{fired['ingestion_id']}", headers=auth_a)
    body = job.json()["data"]
    assert body["trigger"] == "schedule"
    assert body["schedule_id"] == sched["id"]
    assert body["rows_appended"] == len(ROWS)
    types = [e["event_type"] for e in await outbox_events(container, TENANT_A)]
    assert "ingestion.schedule_fired" in types


async def test_overlap_skip_emits_schedule_skipped(client, auth_a, container) -> None:
    """BR-10 / AC-9 mechanics."""
    _conn, sched = await make_schedule(client, auth_a, container)
    async with container.db.tenant_session(TENANT_A) as session:
        session.add(
            Ingestion(
                id=uuid7(),
                tenant_id=TENANT_A,
                workspace_id="00000000-0000-0000-0000-000000000000",
                ingestion_mode="query",
                schedule_id=sched["id"],
                status="running",
            )
        )
        await session.commit()
    resp = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    assert resp.json()["data"] == {"skipped": True}
    skipped = await outbox_events(container, TENANT_A, "ingestion.schedule_skipped")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["overlap_policy"] == "skip"


async def test_overlap_buffer_one_queues_single_pending_run(client, auth_a, container) -> None:
    _conn, sched = await make_schedule(client, auth_a, container, overlap_policy="buffer_one")
    async with container.db.tenant_session(TENANT_A) as session:
        session.add(
            Ingestion(
                id=uuid7(),
                tenant_id=TENANT_A,
                workspace_id="00000000-0000-0000-0000-000000000000",
                ingestion_mode="query",
                schedule_id=sched["id"],
                status="running",
            )
        )
        await session.commit()
    first = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    assert first.json()["data"]["buffered"] is True
    second = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    assert second.json()["data"] == {"skipped": True}  # at most one buffered run


async def test_pause_resume_and_delete(client, auth_a, container) -> None:
    _conn, sched = await make_schedule(client, auth_a, container)
    resp = await client.post(f"/api/v1/schedules/{sched['id']}/pause", headers=auth_a)
    assert resp.json()["data"]["enabled"] is False
    resp = await client.post(f"/api/v1/schedules/{sched['id']}/resume", headers=auth_a)
    assert resp.json()["data"]["enabled"] is True
    resp = await client.delete(f"/api/v1/schedules/{sched['id']}", headers=auth_a)
    assert resp.status_code == 204
    resp = await client.get(f"/api/v1/schedules/{sched['id']}", headers=auth_a)
    assert resp.status_code == 404


async def test_patch_retiming(client, auth_a, container) -> None:
    _conn, sched = await make_schedule(client, auth_a, container)
    resp = await client.patch(
        f"/api/v1/schedules/{sched['id']}", json={"cron": "30 3 * * *"}, headers=auth_a
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["cron"] == "30 3 * * *"
    resp = await client.patch(
        f"/api/v1/schedules/{sched['id']}", json={"cron": "bogus"}, headers=auth_a
    )
    assert resp.status_code == 422


async def test_watermark_spec_validated_at_create(client, auth_a, container) -> None:
    conn = await create_connection(client, auth_a)
    payload = schedule_payload(
        conn["id"],
        watermark={
            "column": "bad;col",
            "value_type": "timestamp",
            "initial_value": "2026-07-01T00:00:00Z",
        },
    )
    resp = await client.post("/api/v1/schedules", json=payload, headers=auth_a)
    assert resp.status_code == 422
