"""Unit: profiling orchestration end-to-end with the in-process runner —
AC-2, AC-3, AC-4 — plus callback signature verification (DST-FR-023) and the
profile re-trigger limits (§4.5)."""

from __future__ import annotations

import hmac
import json
from hashlib import sha256

import pandas as pd

from app.adapters.profiler_runner import sign_callback
from tests.conftest import (
    SPIFFE_INGESTION,
    SPIFFE_PROFILER,
    TENANT_A,
    auth,
    create_dataset,
)

DF = pd.DataFrame(
    {
        "order_id": range(100),
        "order_total": [10.0 + i for i in range(100)],
        "discount_code": [None] * 45 + ["SAVE10"] * 55,
    }
)


async def register_version(client, container, ds, snapshot_id=1001, df=DF, **body):
    await container.catalog.commit_snapshot(ds["iceberg_table"], snapshot_id, df)
    resp = await client.post(
        f"/internal/v1/datasets/{ds['id']}/versions",
        json={
            "tenant_id": TENANT_A,
            "iceberg_snapshot_id": snapshot_id,
            "schema": {c: {"type": "string", "nullable": True, "tags": []}
                       for c in df.columns},
            "row_count": len(df),
            "bytes": 4096,
            "produced_by_urn": f"wr:{TENANT_A}:ingestion:ingestion/i-{snapshot_id}",
            **body,
        },
        headers={"x-client-spiffe-id": SPIFFE_INGESTION},
    )
    return resp


class TestProfileFlow:
    async def test_ac2_profile_completes_end_to_end(self, client, container):
        """AC-2: profiler PUTs result -> objects in store, pointer+summary<=64KB in
        DB, dataset ready, dataset.profile_completed emitted."""
        ds = await create_dataset(client, name="Orders")
        resp = await register_version(client, container, ds)
        assert resp.status_code == 201, resp.text

        state = container.memory_state
        version = next(iter(state.versions.values()))
        assert version.profile_status == "completed"
        profile = state.profiles[version.profile_id]
        assert profile.status == "completed"

        assert await container.object_store.exists(profile.object_key_json)
        assert await container.object_store.exists(profile.object_key_html)
        doc = json.loads(await container.object_store.get(profile.object_key_json))
        assert doc["schema_version"] == 1
        assert doc["table"]["row_count"] == 100

        assert len(json.dumps(profile.summary).encode()) <= 64 * 1024
        assert {c["name"] for c in profile.summary["columns"]} == set(DF.columns)

        dataset = state.datasets[ds["id"]]
        assert dataset.status == "ready"
        assert state.events_of_type("dataset.profile_completed")
        assert state.events_of_type("dataset.version_created")

    async def test_profile_backfills_real_column_types_into_version_schema(
        self, client, container,
    ):
        """The registered schema is always all-"string" (bronze is contractually
        string-typed at ingestion). Once profiling completes, the profiler's real
        `logical_type` per column must be written back into version.schema —
        otherwise semantic-service's authoring validation permanently rejects
        legitimate avg()/time-dimension bindings as type mismatches."""
        ds = await create_dataset(client, name="Orders")
        resp = await register_version(client, container, ds)
        assert resp.status_code == 201, resp.text

        state = container.memory_state
        version = next(iter(state.versions.values()))
        # Registered as all-"string" pre-profiling...
        # ...but post-profiling, the numeric columns must be re-typed from the
        # profiler's inference, not left as "string" forever.
        assert version.schema["order_id"]["type"] != "string"
        assert version.schema["order_total"]["type"] != "string"
        # nullable/tags on the pre-existing schema entry are preserved, not
        # dropped by the merge.
        assert version.schema["order_id"]["nullable"] is True
        assert version.schema["order_id"]["tags"] == []
        # Sanity: the backfilled type actually matches what the profiler summary
        # itself reports for that column (single source of truth).
        profile = state.profiles[version.profile_id]
        by_name = {c["name"]: c["logical_type"] for c in profile.summary["columns"]}
        assert version.schema["order_id"]["type"] == by_name["order_id"]
        assert version.schema["order_total"]["type"] == by_name["order_total"]

    async def test_profile_summary_endpoint_with_signed_urls(self, client, container):
        ds = await create_dataset(client, name="Orders")
        await register_version(client, container, ds)
        resp = await client.get(f"/api/v1/datasets/{ds['id']}/profile", headers=auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "completed"
        assert data["table"]["row_count"] == 100
        assert "expires=" in data["full_json_url"]
        assert "expires=" in data["html_report_url"]
        hn = next(a for a in data["alerts"] if a["flag"] == "HIGH_NULLS")
        assert hn["column"] == "discount_code"

    async def test_ac4_empty_data_fails_profile_not_dataset(self, client, container):
        """AC-4: 0 rows -> profile failed/EMPTY_DATA; dataset still ready."""
        ds = await create_dataset(client, name="Empty")
        empty = pd.DataFrame({"a": pd.Series([], dtype="float64")})
        resp = await register_version(client, container, ds, snapshot_id=2002, df=empty,
                                      row_count=0)
        assert resp.status_code == 201
        state = container.memory_state
        version = next(iter(state.versions.values()))
        profile = state.profiles[version.profile_id]
        assert profile.status == "failed"
        assert profile.error_category == "EMPTY_DATA"
        assert version.profile_status == "failed"
        assert state.datasets[ds["id"]].status == "ready"  # DST-FR-024
        assert state.events_of_type("dataset.profile_failed")

    async def test_skip_profiling(self, client, container):
        ds = await create_dataset(client, name="Bulk")
        resp = await register_version(client, container, ds, skip_profiling=True)
        assert resp.status_code == 201
        state = container.memory_state
        version = next(iter(state.versions.values()))
        assert version.profile_status == "none"
        assert state.datasets[ds["id"]].status == "ready"

    async def test_ac5_schema_change_event(self, client, container):
        """AC-5 via API: v2 drops legacy_code, adds discount -> breaking + event."""
        ds = await create_dataset(client, name="Evolving")
        df1 = pd.DataFrame({"order_id": [1], "legacy_code": ["x"]})
        df2 = pd.DataFrame({"order_id": [1], "discount": [0.1]})
        r1 = await register_version(
            client, container, ds, snapshot_id=1, df=df1, skip_profiling=True,
            schema={"order_id": {"type": "long"}, "legacy_code": {"type": "string"}},
        )
        r2 = await register_version(
            client, container, ds, snapshot_id=2, df=df2, skip_profiling=True,
            schema={"order_id": {"type": "long"}, "discount": {"type": "double"}},
        )
        assert (r1.status_code, r2.status_code) == (201, 201)
        v2 = r2.json()["data"]
        assert v2["version_no"] == 2
        assert v2["breaking_change"] is True
        assert v2["schema_diff"]["added"] == ["discount"]
        assert v2["schema_diff"]["removed"] == ["legacy_code"]
        events = container.memory_state.events_of_type("dataset.schema_changed")
        assert len(events) == 1
        assert events[0]["payload"]["schema_diff"]["removed"] == ["legacy_code"]

    async def test_duplicate_snapshot_registration_409(self, client, container):
        ds = await create_dataset(client, name="Dup")
        await register_version(client, container, ds, snapshot_id=7, skip_profiling=True)
        resp = await register_version(client, container, ds, snapshot_id=7,
                                      skip_profiling=True)
        assert resp.status_code == 409

    async def test_unknown_snapshot_rejected_br1(self, client, container):
        ds = await create_dataset(client, name="NoSnap")
        resp = await client.post(
            f"/internal/v1/datasets/{ds['id']}/versions",
            json={"tenant_id": TENANT_A, "iceberg_snapshot_id": 999999, "schema": {}},
            headers={"x-client-spiffe-id": SPIFFE_INGESTION},
        )
        assert resp.status_code == 409  # BR-1

    async def test_internal_requires_spiffe(self, client):
        resp = await client.post(
            "/internal/v1/datasets/x/versions",
            json={"tenant_id": TENANT_A, "iceberg_snapshot_id": 1},
            headers={"x-client-spiffe-id": "spiffe://evil/sa/nope"},
        )
        assert resp.status_code == 403


class TestRetriggerLimits:
    async def test_retrigger_conflict_while_pending(self, recording_client,
                                                    recording_container):
        ds = await create_dataset(recording_client, name="Orders")
        resp = await register_version(recording_client, recording_container, ds)
        assert resp.status_code == 201  # profile stays pending (recording runner)
        resp = await recording_client.post(
            f"/api/v1/datasets/{ds['id']}/versions/1/profile", headers=auth()
        )
        assert resp.status_code == 409

    async def test_rate_limit_3_per_hour(self, client, container, clock):
        ds = await create_dataset(client, name="Orders")
        await register_version(client, container, ds)  # profile #1 (completes)
        for _ in range(2):
            resp = await client.post(
                f"/api/v1/datasets/{ds['id']}/versions/1/profile", headers=auth()
            )
            assert resp.status_code == 202  # completes synchronously each time
        resp = await client.post(
            f"/api/v1/datasets/{ds['id']}/versions/1/profile", headers=auth()
        )
        assert resp.status_code == 429
        clock.advance(hours=2)
        resp = await client.post(
            f"/api/v1/datasets/{ds['id']}/versions/1/profile", headers=auth()
        )
        assert resp.status_code == 202


class TestTimeoutSweep:
    async def test_ac3_timeout_retry_then_failed(self, recording_client,
                                                 recording_container, clock):
        """AC-3: >30min -> kill + one retry; second timeout -> failed/TIMEOUT while
        version and dataset end up ready with profile_status=failed."""
        c = recording_container
        ds = await create_dataset(recording_client, name="Slow")
        await register_version(recording_client, c, ds)
        runner = c.runner
        assert len(runner.specs) == 1

        ctx = c.dataset_service  # any service; build CallCtx via principal-free path
        from app.domain.services import CallCtx

        call = CallCtx(tenant_id=TENANT_A, actor={"type": "service", "id": "scheduler"})

        clock.advance(minutes=31)
        acted = await c.profile_service.sweep_timeouts(call)
        assert acted == 1
        assert len(runner.specs) == 2  # one automatic retry relaunched
        state = c.memory_state
        profile = next(iter(state.profiles.values()))
        assert profile.attempt == 2
        assert profile.status == "pending"

        clock.advance(minutes=31)
        acted = await c.profile_service.sweep_timeouts(call)
        assert acted == 1
        profile = next(iter(state.profiles.values()))
        assert profile.status == "failed"
        assert profile.error_category == "TIMEOUT"
        version = next(iter(state.versions.values()))
        assert version.profile_status == "failed"
        assert state.datasets[ds["id"]].status == "ready"
        assert state.events_of_type("dataset.profile_failed")
        _ = ctx


class TestCallbackSecurity:
    async def test_bad_signature_rejected(self, recording_client, recording_container):
        ds = await create_dataset(recording_client, name="Sig")
        await register_version(recording_client, recording_container, ds)
        spec = recording_container.runner.specs[0]
        body = json.dumps({"tenant_id": TENANT_A, "status": "failed",
                           "error_category": "INTERNAL"}).encode()
        resp = await recording_client.put(
            f"/internal/v1/profiles/{spec.profile_id}",
            content=body,
            headers={"content-type": "application/json",
                     "x-client-spiffe-id": SPIFFE_PROFILER,
                     "x-profiler-signature": "deadbeef"},
        )
        assert resp.status_code == 403

    async def test_valid_signature_accepted_and_terminal_conflict(
        self, recording_client, recording_container
    ):
        ds = await create_dataset(recording_client, name="Sig2")
        await register_version(recording_client, recording_container, ds)
        spec = recording_container.runner.specs[0]
        body = json.dumps({"tenant_id": TENANT_A, "status": "failed",
                           "error_category": "INTERNAL"}).encode()
        sig = sign_callback(spec.callback_token, body)
        assert sig == hmac.new(spec.callback_token.encode(), body, sha256).hexdigest()
        resp = await recording_client.put(
            f"/internal/v1/profiles/{spec.profile_id}",
            content=body,
            headers={"content-type": "application/json",
                     "x-client-spiffe-id": SPIFFE_PROFILER,
                     "x-profiler-signature": sig},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "failed"
        # terminal profile -> further callbacks conflict (DST-FR-023 "409 terminal")
        resp = await recording_client.put(
            f"/internal/v1/profiles/{spec.profile_id}",
            content=body,
            headers={"content-type": "application/json",
                     "x-client-spiffe-id": SPIFFE_PROFILER,
                     "x-profiler-signature": sig},
        )
        assert resp.status_code == 409

    async def test_oversized_summary_rejected_422(
        self, recording_client, recording_container
    ):
        """BR-4 / MASTER-FR-061: a completed callback whose summary exceeds 64KB is
        rejected 422 (no-blob rule) — drives the guard at ProfileService.complete
        that the DB CHECK also backstops. Profile stays non-terminal."""
        c = recording_container
        ds = await create_dataset(recording_client, name="BigSummary")
        await register_version(recording_client, c, ds)
        spec = c.runner.specs[0]

        # Objects must exist so completion reaches the summary-size guard.
        key_json = f"{spec.output_prefix}/profile.json"
        key_html = f"{spec.output_prefix}/profile.html"
        await c.object_store.put(key_json, b"{}", "application/json")
        await c.object_store.put(key_html, b"<html></html>", "text/html")

        oversized = {
            "table": {"row_count": 1, "column_count": 3000},
            "columns": [
                {"name": f"column_with_long_name_{i:04d}", "logical_type": "string",
                 "null_pct": 0.0, "distinct_count": 1, "quality_flags": ["FILLER" * 6]}
                for i in range(3000)
            ],
            "alerts": [],
        }
        body = json.dumps({
            "tenant_id": TENANT_A, "status": "completed",
            "object_key_json": key_json, "object_key_html": key_html,
            "summary": oversized,
            "sample": {"strategy": "full", "fraction": 1.0, "seed": 42},
        }).encode()
        assert len(json.dumps(oversized).encode()) > 64 * 1024

        sig = sign_callback(spec.callback_token, body)
        resp = await recording_client.put(
            f"/internal/v1/profiles/{spec.profile_id}",
            content=body,
            headers={"content-type": "application/json",
                     "x-client-spiffe-id": SPIFFE_PROFILER,
                     "x-profiler-signature": sig},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_FAILED"
        # Rejected mid-completion: the profile was not marked completed.
        profile = c.memory_state.profiles[spec.profile_id]
        assert profile.status != "completed"
        assert profile.summary is None
