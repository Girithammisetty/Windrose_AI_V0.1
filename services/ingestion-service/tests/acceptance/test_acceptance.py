"""BRD 03 §10 acceptance criteria, one named test per AC.

AC-4's 10GiB/512MiB release-gate perf test is implemented scaled-down
(~200MiB stream, peak RSS delta < 100MiB) per the build instructions; the
full-size run is a CI release gate on reference hardware (see README).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import json
import resource
import sys
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.api.auth import Principal
from app.api.schemas import (
    IngestionCreate,
    NewDataset,
    PartManifestEntry,
    UploadComplete,
    UploadCreate,
)
from app.config import MIB, Settings
from app.container import build_container
from app.domain.querysource import FakeQuerySource
from app.domain.services.ingestions import IngestionService
from app.domain.services.uploads import UploadService
from app.store.models import Connection
from tests.util import (
    AUDIENCE,
    ISSUER,
    TENANT_A,
    TENANT_B,
    VALID_PG_CONNECTION,
    create_connection,
    csv_blob,
    outbox_events,
    slice_parts,
    upload_file_flow,
)

# --------------------------------------------------------------------------- AC-1


async def test_ac01_connection_secret_only_in_vault(client, auth_a, container) -> None:
    created = await create_connection(client, auth_a)
    # password exists only in the secrets store
    assert "s3cr3t-pw" in container.secrets.dump_all_values()
    async with container.db.tenant_session(TENANT_A) as session:
        conn = (
            await session.execute(sa.select(Connection).where(Connection.id == created["id"]))
        ).scalar_one()
        assert "s3cr3t-pw" not in json.dumps(conn.config)
        assert conn.vault_ref.startswith(f"secret/data/tenants/{TENANT_A}/connections/")
    # GET returns secret_set true, no secret material
    resp = await client.get(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert resp.json()["data"]["secret_set"] is True
    assert "s3cr3t-pw" not in resp.text
    events = await outbox_events(container, TENANT_A, "connection.created")
    assert len(events) == 1


# --------------------------------------------------------------------------- AC-2


async def test_ac02_unreachable_host_424_nothing_persisted(client, auth_a, container) -> None:
    payload = {
        **VALID_PG_CONNECTION,
        "config": {**VALID_PG_CONNECTION["config"], "host": "unreachable.db.internal"},
    }
    resp = await client.post("/api/v1/connections", json=payload, headers=auth_a)
    assert resp.status_code == 424
    error = resp.json()["error"]
    assert error["code"] == "CONNECTION_TEST_FAILED"
    assert error["details"]["error_category"] == "SOURCE_UNREACHABLE"
    async with container.db.tenant_session(TENANT_A) as session:
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(Connection))
        ).scalar_one()
    assert count == 0
    assert container.secrets.dump_all_values() == []


# --------------------------------------------------------------------------- AC-3


async def test_ac03_cross_tenant_read_404_and_audited(client, auth_a, auth_b, container) -> None:
    created = await create_connection(client, auth_b)  # tenant B's resource
    resp = await client.get(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    denied = await outbox_events(container, TENANT_A, "security.cross_tenant_denied")
    assert len(denied) == 1
    assert denied[0]["payload"]["resource_id"] == created["id"]


# --------------------------------------------------------------------------- AC-4 (scaled)


def _rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


async def test_ac04_scaled_200mib_upload_bounded_memory(tmp_path, rsa_keys) -> None:
    """Scaled 10GiB/512MiB gate: ~200MiB synthetic CSV streamed through the
    full upload+decode+append path; peak RSS delta must stay < 100MiB and
    exactly one snapshot lands with an exact row count."""
    _, public_pem = rsa_keys
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/perf.db",
        data_dir=str(tmp_path / "data"),
        jwt_public_key_pem=public_pem.decode(),
        inline_execution=True,
        retry_backoff_base_s=0.0,
        progress_min_interval_s=5.0,
        min_part_size=8 * MIB,
        default_part_size=8 * MIB,
    )
    container = build_container(settings)
    await container.db.create_all()
    principal = Principal(sub="perf-user", tenant_id=TENANT_A, typ="user")

    header = b"a,b,c\n"
    row = b"0," + b"v" * 195 + b",7\n"  # 200 bytes exactly
    assert len(row) == 200
    n_rows = (200 * MIB) // 200  # 1,048,576 rows -> 200 MiB of row data
    block = row * 512  # 100 KiB repeating block
    n_blocks = n_rows // 512
    total_bytes = len(header) + n_rows * 200
    part_size = 8 * MIB

    _status, job = await IngestionService(container).create(
        principal,
        IngestionCreate(
            ingestion_mode="file_upload", file_format="csv", new_dataset=NewDataset(name="perf")
        ),
    )
    upload_svc = UploadService(container)
    upload = await upload_svc.create(
        principal,
        UploadCreate(ingestion_id=job["id"], part_size=part_size, bytes_total=total_bytes),
    )

    async def whole_stream():
        yield header
        for _ in range(n_blocks):
            yield block

    source = whole_stream()
    leftover = bytearray()

    def next_part_stream():
        async def gen():
            nonlocal leftover
            remaining = part_size
            while remaining > 0:
                if leftover:
                    take = bytes(leftover[:remaining])
                    del leftover[: len(take)]
                else:
                    try:
                        chunk = await anext(source)
                    except StopAsyncIteration:
                        return
                    take = chunk[:remaining]
                    leftover = bytearray(chunk[len(take) :])
                remaining -= len(take)
                yield take

        return gen()

    baseline = _rss_bytes()

    n_parts = -(-total_bytes // part_size)
    manifest = []
    for n in range(1, n_parts + 1):
        result = await upload_svc.put_part(principal, upload["upload_id"], n, next_part_stream())
        manifest.append(PartManifestEntry(n=result["n"], etag=result["etag"], size=result["size"]))

    status, finished = await upload_svc.complete(
        principal, upload["upload_id"], UploadComplete(parts=manifest)
    )
    peak_delta = _rss_bytes() - baseline

    assert status == 202
    assert finished["status"] == "completed"
    assert finished["rows_appended"] == n_rows  # matches source row count
    assert finished["bytes_received"] == total_bytes
    snapshots = container.table_writer.all_snapshots()
    assert len(snapshots) == 1  # exactly one Iceberg snapshot (BR-9)
    assert snapshots[0]["summary"]["ingestion_id"] == finished["id"]
    assert peak_delta < 100 * MIB, f"peak RSS grew by {peak_delta / MIB:.1f} MiB"
    await container.db.dispose()


# --------------------------------------------------------------------------- AC-5


async def test_ac05_resume_sends_only_missing_parts(client, auth_a) -> None:
    blob = csv_blob(200)
    part_size = 512
    parts = slice_parts(blob, part_size)
    assert len(parts) >= 5

    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "file_upload",
            "file_format": "csv",
            "new_dataset": {"name": "resume"},
        },
        headers=auth_a,
    )
    job = resp.json()["data"]
    resp = await client.post(
        "/api/v1/uploads",
        json={"ingestion_id": job["id"], "part_size": part_size},
        headers=auth_a,
    )
    upload_id = resp.json()["data"]["upload_id"]

    confirmed_before = {}
    interrupted_at = len(parts) - 2  # "laptop slept" before the last two parts
    for n in range(1, interrupted_at + 1):
        resp = await client.put(
            f"/api/v1/uploads/{upload_id}/parts/{n}", content=parts[n - 1], headers=auth_a
        )
        confirmed_before[n] = resp.json()["data"]

    # client resumes: asks which parts are confirmed
    resp = await client.get(f"/api/v1/uploads/{upload_id}", headers=auth_a)
    state = resp.json()["data"]
    confirmed_ns = {p["n"] for p in state["parts"]}
    assert confirmed_ns == set(range(1, interrupted_at + 1))

    missing = [n for n in range(1, len(parts) + 1) if n not in confirmed_ns]
    assert missing == [len(parts) - 1, len(parts)]
    manifest = list(state["parts"])
    for n in missing:  # re-send ONLY missing parts
        resp = await client.put(
            f"/api/v1/uploads/{upload_id}/parts/{n}", content=parts[n - 1], headers=auth_a
        )
        manifest.append(resp.json()["data"])

    resp = await client.post(
        f"/api/v1/uploads/{upload_id}/complete", json={"parts": manifest}, headers=auth_a
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["data"]["status"] == "completed"
    assert resp.json()["data"]["rows_appended"] == 200


# --------------------------------------------------------------------------- AC-6


async def test_ac06_progress_events_monotonic(client, auth_a, container) -> None:
    container.settings.decode_batch_size = 25  # force several progress ticks
    job = await upload_file_flow(client, auth_a, csv_blob(150), part_size=1024)
    assert job["status"] == "completed"
    events = await outbox_events(container, TENANT_A, "ingestion.progress")
    events = [e for e in events if e["payload"]["ingestion_id"] == job["id"]]
    assert len(events) >= 2  # scaled stand-in for ">= one event per 5s"
    rows_seq = [e["payload"]["rows_appended"] for e in events]
    bytes_seq = [e["payload"]["bytes_received"] for e in events]
    assert rows_seq == sorted(rows_seq)
    assert bytes_seq == sorted(bytes_seq)
    assert all(e["payload"]["phase"] == "decoding" for e in events)


# --------------------------------------------------------------------------- AC-7


async def test_ac07_empty_query_result_fails_decode_error(client, auth_a, container) -> None:
    container.query_sources.set("postgres", FakeQuerySource(rows=[]))
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT * FROM empty_table",
            "new_dataset": {"name": "empty"},
            "allow_empty": False,
        },
        headers=auth_a,
    )
    job = resp.json()["data"]
    assert job["status"] == "failed"
    assert job["error_log"]["category"] == "DECODE_ERROR"
    assert job["error_log"]["hint"]
    assert container.table_writer.all_snapshots() == []  # no snapshot created


# --------------------------------------------------------------------------- AC-8


async def test_ac08_watermark_bound_as_parameter_across_runs(client, auth_a, container) -> None:
    fake = FakeQuerySource(
        rows=[
            {"id": 1, "updated_at": "2026-06-30T00:00:00+00:00"},  # before initial watermark
            {"id": 2, "updated_at": "2026-07-02T00:00:00+00:00"},
            {"id": 3, "updated_at": "2026-07-05T00:00:00+00:00"},
        ]
    )
    container.query_sources.set("postgres", fake)
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/schedules",
        json={
            "connection_id": conn["id"],
            "cron": "0 2 * * *",
            "timezone": "Europe/Berlin",
            "ingestion_template": {
                "ingestion_mode": "query",
                "statement": "SELECT * FROM public.orders",
                "new_dataset": {"name": "orders"},
            },
            "watermark": {
                "column": "updated_at",
                "operator": ">",
                "value_type": "timestamp",
                "initial_value": "2026-07-01T00:00:00Z",
            },
            "overlap_policy": "skip",
            "enabled": True,
        },
        headers=auth_a,
    )
    assert resp.status_code == 201, resp.text
    sched = resp.json()["data"]

    # run 1: binds the initial watermark, appends the 2 newer rows
    run1 = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    assert run1.json()["data"]["status"] == "completed"
    sql1, params1 = fake.calls[-1]
    assert sql1.endswith("src WHERE updated_at > :watermark")
    assert "2026" not in sql1  # no literal splicing (query-log assertion)
    assert params1["watermark"] == datetime(2026, 7, 1, tzinfo=UTC)
    job1 = await client.get(
        f"/api/v1/ingestions/{run1.json()['data']['ingestion_id']}", headers=auth_a
    )
    assert job1.json()["data"]["rows_appended"] == 2

    # source gains one newer row
    fake.rows.append({"id": 4, "updated_at": "2026-07-08T00:00:00+00:00"})

    # run 2: binds the max watermark observed in run 1 as a typed parameter
    run2 = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    sql2, params2 = fake.calls[-1]
    assert params2["watermark"] == datetime(2026, 7, 5, tzinfo=UTC)
    assert "2026-07-05" not in sql2
    job2 = await client.get(
        f"/api/v1/ingestions/{run2.json()['data']['ingestion_id']}", headers=auth_a
    )
    assert job2.json()["data"]["rows_appended"] == 1  # only the newer row

    state = await client.get(f"/api/v1/schedules/{sched['id']}", headers=auth_a)
    assert state.json()["data"]["watermark"]["current_value"] == "2026-07-08T00:00:00+00:00"


# --------------------------------------------------------------------------- AC-9


async def test_ac09_overlap_skip_no_job_and_event(client, auth_a, container) -> None:
    from app.ids import uuid7
    from app.store.models import Ingestion

    container.query_sources.set("postgres", FakeQuerySource(rows=[{"id": 1}]))
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/schedules",
        json={
            "connection_id": conn["id"],
            "cron": "0 2 * * *",
            "timezone": "UTC",
            "ingestion_template": {
                "ingestion_mode": "query",
                "statement": "SELECT 1",
                "new_dataset": {"name": "x"},
            },
            "overlap_policy": "skip",
        },
        headers=auth_a,
    )
    sched = resp.json()["data"]
    async with container.db.tenant_session(TENANT_A) as session:
        session.add(
            Ingestion(
                id=uuid7(),
                tenant_id=TENANT_A,
                workspace_id="00000000-0000-0000-0000-000000000000",
                ingestion_mode="query",
                schedule_id=sched["id"],
                status="running",  # previous run still active
            )
        )
        await session.commit()
    before = await client.get(
        "/api/v1/ingestions", params={"filter[schedule_id]": sched["id"]}, headers=auth_a
    )
    n_before = len(before.json()["data"])
    fired = await client.post(f"/api/v1/schedules/{sched['id']}/run_now", headers=auth_a)
    assert fired.json()["data"] == {"skipped": True}
    after = await client.get(
        "/api/v1/ingestions", params={"filter[schedule_id]": sched["id"]}, headers=auth_a
    )
    assert len(after.json()["data"]) == n_before  # no new job created
    skipped = await outbox_events(container, TENANT_A, "ingestion.schedule_skipped")
    assert len(skipped) == 1


# -------------------------------------------------------------------------- AC-10


async def test_ac10_connection_delete_guard_then_vault_destroy(client, auth_a, container) -> None:
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/schedules",
        json={
            "connection_id": conn["id"],
            "cron": "0 2 * * *",
            "timezone": "UTC",
            "ingestion_template": {
                "ingestion_mode": "query",
                "statement": "SELECT 1",
                "new_dataset": {"name": "x"},
            },
            "enabled": True,
        },
        headers=auth_a,
    )
    sched = resp.json()["data"]

    resp = await client.delete(f"/api/v1/connections/{conn['id']}", headers=auth_a)
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["enabled_schedules"] == 1

    await client.delete(f"/api/v1/schedules/{sched['id']}", headers=auth_a)
    resp = await client.delete(f"/api/v1/connections/{conn['id']}", headers=auth_a)
    assert resp.status_code == 204

    destroys = container.secrets.scheduled_destroys
    path = next(p for p in destroys if conn["id"] in p)
    eta = destroys[path] - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < eta < timedelta(days=7, hours=1)  # +7d grace


# -------------------------------------------------------------------------- AC-11


async def test_ac11_webhook_hmac_and_event_id_dedup(client, auth_a, container) -> None:
    # POST /ingestions rejects webhook_batch with an honest 501 while the
    # buffer->Iceberg flush is unimplemented (see IngestionService._validate_mode),
    # so seed the ingestion + endpoint + Vault secret directly to exercise the
    # real receive path: HMAC auth, event_id dedup, exact-once accounting.
    from app.domain.secrets import webhook_secret_path
    from app.ids import uuid7
    from app.store.models import Ingestion, WebhookEndpoint

    gated = await client.post(
        "/api/v1/ingestions",
        json={"ingestion_mode": "webhook_batch", "new_dataset": {"name": "hooks"}},
        headers=auth_a,
    )
    assert gated.status_code == 501
    assert gated.json()["error"]["code"] == "NOT_IMPLEMENTED"

    ingestion_id = uuid7()
    path_token = f"{TENANT_A}.seeded-hook-token"
    signing_secret = "ab" * 32
    vault_ref = webhook_secret_path(TENANT_A, ingestion_id)
    async with container.db.tenant_session(TENANT_A) as session:
        session.add(
            Ingestion(
                id=ingestion_id,
                tenant_id=TENANT_A,
                workspace_id="00000000-0000-0000-0000-000000000000",
                ingestion_mode="webhook_batch",
                dataset_urn=f"wr:{TENANT_A}:dataset:dataset/{uuid7()}",
                status="queued",
            )
        )
        session.add(
            WebhookEndpoint(
                id=uuid7(),
                tenant_id=TENANT_A,
                ingestion_id=ingestion_id,
                path_token=path_token,
                hmac_vault_ref=vault_ref,
            )
        )
        await session.commit()
    await container.secrets.put(vault_ref, {"signing_secret": signing_secret})

    data = {"id": ingestion_id}
    url = f"/api/v1/hooks/{path_token}/events"
    secret = signing_secret.encode()

    body = json.dumps({"event_id": "evt-1", "value": 42}).encode()

    # invalid signature -> 401, nothing buffered / counted
    bad = await client.post(url, content=body, headers={"X-Windrose-Signature": "0" * 64})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "SIGNATURE_INVALID"
    job = (await client.get(f"/api/v1/ingestions/{data['id']}", headers=auth_a)).json()["data"]
    assert job["rows_appended"] == 0

    def sign(payload: bytes) -> dict[str, str]:
        return {"X-Windrose-Signature": hmac_mod.new(secret, payload, hashlib.sha256).hexdigest()}

    ok = await client.post(url, content=body, headers=sign(body))
    assert ok.status_code == 202
    assert ok.json()["data"] == {"accepted": 1, "duplicates": 0}

    # duplicate event_id within 24h: acknowledged, not double-counted
    dup = await client.post(url, content=body, headers=sign(body))
    assert dup.status_code == 202
    assert dup.json()["data"] == {"accepted": 0, "duplicates": 1}

    job = (await client.get(f"/api/v1/ingestions/{data['id']}", headers=auth_a)).json()["data"]
    assert job["rows_appended"] == 1


# -------------------------------------------------------------------------- AC-12


async def test_ac12_transient_retries_then_manual_retry_no_duplicates(
    client, auth_a, container
) -> None:
    flaky = FakeQuerySource(rows=[{"id": 1}, {"id": 2}], fail_attempts=99)  # outage never heals
    container.query_sources.set("postgres", flaky)
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT * FROM flaky",
            "new_dataset": {"name": "flaky"},
        },
        headers=auth_a,
    )
    job = resp.json()["data"]
    assert job["status"] == "failed"
    assert job["attempts"] == 5  # 5 attempts with backoff (ING-FR-081)
    assert job["error_log"]["category"] == "SOURCE_UNREACHABLE"
    assert len(flaky.calls) == 5
    assert container.table_writer.all_snapshots() == []

    # source recovers; POST /retry produces a fresh successful run
    container.query_sources.set("postgres", FakeQuerySource(rows=[{"id": 1}, {"id": 2}]))
    resp = await client.post(f"/api/v1/ingestions/{job['id']}/retry", headers=auth_a)
    assert resp.status_code == 202
    clone = resp.json()["data"]
    assert clone["status"] == "completed"
    assert clone["rows_appended"] == 2

    # BR-9 verified via snapshot summary: exactly one snapshot, owned by the retry
    snapshots = container.table_writer.all_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0]["summary"]["ingestion_id"] == clone["id"]


# -------------------------------------------------------------------------- AC-13


async def test_ac13_row_limit_exceeded_with_truncated_samples(client, auth_a, container) -> None:
    long_junk = "j" * 400
    good = "\n".join(f"{i},ok-{i}" for i in range(10))
    bad = "\n".join(f"{long_junk}" for _ in range(150))  # 150 undecodable rows
    content = f"id,name\n{good}\n{bad}\n".encode()

    job = await upload_file_flow(client, auth_a, content, part_size=1024, error_row_limit=100)
    assert job["status"] == "failed"
    assert job["error_log"]["category"] == "ROW_LIMIT_EXCEEDED"
    samples = job["error_log"]["samples"]
    assert 0 < len(samples) <= 20
    assert all(len(s["raw"]) <= 256 for s in samples)
    assert container.table_writer.all_snapshots() == []


# -------------------------------------------------------------------------- AC-14


async def test_ac14_concurrent_same_idempotency_key_single_job(client, auth_a) -> None:
    headers = {**auth_a, "Idempotency-Key": "concurrent-key-1"}
    payload = {
        "ingestion_mode": "file_upload",
        "file_format": "csv",
        "new_dataset": {"name": "idem"},
    }
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/ingestions", json=payload, headers=headers),
        client.post("/api/v1/ingestions", json=payload, headers=headers),
    )
    assert {r1.status_code, r2.status_code} == {201}
    replay_flags = [r.headers.get("Idempotency-Replayed") == "true" for r in (r1, r2)]
    assert sorted(replay_flags) == [False, True]  # exactly one replayed
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]

    listing = await client.get(
        "/api/v1/ingestions", params={"filter[ingestion_mode]": "file_upload"}, headers=auth_a
    )
    assert len(listing.json()["data"]) == 1  # one job exists


# sanity: the JWT plumbing used across these tests is RS256 with iss/aud pinned
def test_token_configuration_sane() -> None:
    assert ISSUER.startswith("https://") and AUDIENCE == "windrose"
    assert TENANT_A != TENANT_B
