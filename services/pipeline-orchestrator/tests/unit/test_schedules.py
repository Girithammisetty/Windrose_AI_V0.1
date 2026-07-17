"""Recurring pipeline scheduling (PIPE-FR-050): cron→next_fire, create validation,
the working fire_due mechanism (creates a run + advances next_fire; skips not-yet-due
and disabled), pause/resume, run-now, and tenant isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.entities import CallCtx
from app.domain.enums import RunStatus
from app.domain.scheduler import compute_next_fire
from tests.conftest import TENANT_A, TENANT_B, WORKSPACE, auth

pytestmark = pytest.mark.asyncio


async def _training_template(client, tenant=TENANT_A, name="sched-train"):
    body = {"workspace_id": WORKSPACE, "mode": "train",
            "dataset_refs": {"TRAIN": "wr:t:dataset:dataset/claims"},
            "parameters": {"label_column": "is_fraud"}, "name": name}
    r = await client.post("/api/v1/algorithm-templates/xgboost/pipelines", json=body,
                          headers=auth(tenant))
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


# ------------------------------------------------------------- compute_next_fire

async def test_compute_next_fire_hourly_is_next_top_of_hour():
    now = datetime(2026, 1, 1, 10, 30, tzinfo=UTC)
    nxt = compute_next_fire("0 * * * *", "UTC", now)
    assert nxt == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    assert nxt.tzinfo == UTC


async def test_compute_next_fire_respects_timezone_returns_utc():
    # 00:00 in New York (UTC-5 in January) → 05:00 UTC.
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    nxt = compute_next_fire("0 0 * * *", "America/New_York", now)
    assert nxt == datetime(2026, 1, 2, 5, 0, tzinfo=UTC)


# --------------------------------------------------------------------- create

async def test_create_schedule_computes_next_fire(client):
    tid = await _training_template(client)
    r = await client.post("/api/v1/pipeline-schedules",
                          json={"template_id": tid, "cron": "0 * * * *",
                                "name": "hourly-retrain"}, headers=auth())
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["enabled"] is True
    assert data["next_fire_at"] is not None
    assert data["last_run_id"] is None


async def test_create_rejects_bad_cron(client):
    tid = await _training_template(client)
    r = await client.post("/api/v1/pipeline-schedules",
                          json={"template_id": tid, "cron": "not a cron"},
                          headers=auth())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_create_unknown_template_404(client):
    r = await client.post("/api/v1/pipeline-schedules",
                          json={"template_id": "00000000-0000-4000-8000-000000000000",
                                "cron": "0 * * * *"}, headers=auth())
    assert r.status_code == 404


# ------------------------------------------------------------------- fire_due

async def test_fire_due_creates_run_and_advances_next_fire(client, container):
    tid = await _training_template(client)
    r = await client.post("/api/v1/pipeline-schedules",
                          json={"template_id": tid, "cron": "*/5 * * * *",
                                "run_parameters": {
                                    "training_data": [{"a": 1, "is_fraud": "no"}],
                                    "label_column": "is_fraud"}},
                          headers=auth())
    sid = r.json()["data"]["id"]
    before = r.json()["data"]["next_fire_at"]

    # Fire well after next_fire_at → one run created, next_fire advanced.
    fired = await container.schedule_service.fire_due(
        now=datetime.now(UTC) + timedelta(hours=1))
    assert len(fired) == 1
    run = fired[0]
    assert run.status == int(RunStatus.submitted)

    sched = (await client.get(f"/api/v1/pipeline-schedules/{sid}",
                              headers=auth())).json()["data"]
    assert sched["last_run_id"] == run.id
    assert sched["last_fire_at"] is not None
    assert sched["next_fire_at"] != before  # advanced

    # The created run really exists and carries the schedule trigger in its event.
    got = (await client.get(f"/api/v1/runs/{run.id}", headers=auth())).json()["data"]
    assert got["submitted_by"] == "pipeline-scheduler"
    triggers = {x["payload"]["payload"].get("trigger")
                for x in container.memory_state.outbox
                if x["payload"]["event_type"] == "pipeline.run.submitted"}
    assert "schedule" in triggers


async def test_fire_due_skips_not_yet_due(client, container):
    tid = await _training_template(client)
    await client.post("/api/v1/pipeline-schedules",
                      json={"template_id": tid, "cron": "0 * * * *"}, headers=auth())
    # now is BEFORE next_fire_at (schedule was just created for the next hour).
    fired = await container.schedule_service.fire_due(now=datetime.now(UTC))
    assert fired == []


async def test_pause_stops_firing_resume_restarts(client, container):
    tid = await _training_template(client)
    r = await client.post("/api/v1/pipeline-schedules",
                          json={"template_id": tid, "cron": "*/5 * * * *",
                                "run_parameters": {
                                    "training_data": [{"a": 1, "label": "x"}]}},
                          headers=auth())
    sid = r.json()["data"]["id"]
    future = datetime.now(UTC) + timedelta(hours=1)

    p = await client.post(f"/api/v1/pipeline-schedules/{sid}/pause", headers=auth())
    assert p.json()["data"]["enabled"] is False
    assert await container.schedule_service.fire_due(now=future) == []

    res = await client.post(f"/api/v1/pipeline-schedules/{sid}/resume", headers=auth())
    assert res.json()["data"]["enabled"] is True
    fired = await container.schedule_service.fire_due(now=future)
    assert len(fired) == 1


async def test_run_now_fires_immediately_without_advancing(client, container):
    tid = await _training_template(client)
    r = await client.post("/api/v1/pipeline-schedules",
                          json={"template_id": tid, "cron": "0 0 1 1 *",  # yearly
                                "run_parameters": {
                                    "training_data": [{"a": 1, "label": "x"}]}},
                          headers=auth())
    sid = r.json()["data"]["id"]
    next_before = r.json()["data"]["next_fire_at"]

    rn = await client.post(f"/api/v1/pipeline-schedules/{sid}/run-now", headers=auth())
    assert rn.status_code == 202
    assert rn.json()["run"]["status"] in ("submitted", "running", "succeeded")

    sched = (await client.get(f"/api/v1/pipeline-schedules/{sid}",
                              headers=auth())).json()["data"]
    assert sched["last_run_id"] is not None
    assert sched["next_fire_at"] == next_before  # run-now does NOT advance the cron


async def test_delete_schedule(client, container):
    tid = await _training_template(client)
    sid = (await client.post("/api/v1/pipeline-schedules",
                             json={"template_id": tid, "cron": "0 * * * *"},
                             headers=auth())).json()["data"]["id"]
    d = await client.delete(f"/api/v1/pipeline-schedules/{sid}", headers=auth())
    assert d.status_code == 204
    assert (await client.get(f"/api/v1/pipeline-schedules/{sid}",
                             headers=auth())).status_code == 404


# --------------------------------------------------------------- tenant isolation

async def test_schedule_tenant_isolation(client, container):
    tid = await _training_template(client, tenant=TENANT_A)
    sid = (await client.post("/api/v1/pipeline-schedules",
                             json={"template_id": tid, "cron": "0 * * * *"},
                             headers=auth(TENANT_A))).json()["data"]["id"]

    # Tenant B cannot see or control tenant A's schedule.
    assert (await client.get(f"/api/v1/pipeline-schedules/{sid}",
                             headers=auth(TENANT_B))).status_code == 404
    assert (await client.get("/api/v1/pipeline-schedules",
                             headers=auth(TENANT_B))).json()["data"] == []
    assert (await client.post(f"/api/v1/pipeline-schedules/{sid}/pause",
                              headers=auth(TENANT_B))).status_code == 404


async def test_fire_due_creates_run_in_owning_tenant_only(client, container):
    # A's schedule fires a run visible to A, not B (fire_due scans cross-tenant but
    # writes go through the owning tenant's session).
    tid = await _training_template(client, tenant=TENANT_A)
    r = await client.post("/api/v1/pipeline-schedules",
                          json={"template_id": tid, "cron": "*/5 * * * *",
                                "run_parameters": {
                                    "training_data": [{"a": 1, "label": "x"}]}},
                          headers=auth(TENANT_A))
    _ = r
    fired = await container.schedule_service.fire_due(
        now=datetime.now(UTC) + timedelta(hours=1))
    assert len(fired) == 1
    run_id = fired[0].id
    assert (await client.get(f"/api/v1/runs/{run_id}",
                             headers=auth(TENANT_A))).status_code == 200
    assert (await client.get(f"/api/v1/runs/{run_id}",
                             headers=auth(TENANT_B))).status_code == 404


_ = CallCtx
