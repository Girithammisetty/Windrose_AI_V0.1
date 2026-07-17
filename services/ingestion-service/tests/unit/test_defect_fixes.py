"""Regression tests for defects F3 (timeout enforcement) and F5 (no orphaned
secret on failed create). F2 (cross-tenant audit under RLS) is covered in the
integration tier where real RLS applies."""

from __future__ import annotations

from app.domain.probers import FakeConnectionProber, FakeSourcePreviewer, ProberRegistry
from app.domain.querysource import FakeQuerySource
from app.domain.services.ingestions import IngestionService
from app.ids import uuid7
from app.store.models import Ingestion
from tests.util import TENANT_A, VALID_PG_CONNECTION, create_connection

WORKSPACE = "00000000-0000-0000-0000-000000000000"


# ------------------------------------------------------------------ F3 timeouts


async def test_f3_connection_test_timeout_on_create_returns_424(client, auth_a, container) -> None:
    container.settings.connection_test_timeout_s = 0.05
    container.probers = ProberRegistry(default=FakeConnectionProber(delay_s=1.0))
    resp = await client.post("/api/v1/connections", json=VALID_PG_CONNECTION, headers=auth_a)
    assert resp.status_code == 424
    error = resp.json()["error"]
    assert error["code"] == "CONNECTION_TEST_FAILED"
    assert error["details"]["error_category"] == "TIMEOUT"
    # AC-2 still holds under a timeout: nothing persisted, no secret written
    listing = await client.get("/api/v1/connections", headers=auth_a)
    assert listing.json()["data"] == []
    assert container.secrets.dump_all_values() == []


async def test_f3_saved_connection_test_timeout(client, auth_a, container) -> None:
    created = await create_connection(client, auth_a)  # created with the fast prober
    container.settings.connection_test_timeout_s = 0.05
    container.probers = ProberRegistry(default=FakeConnectionProber(delay_s=1.0))
    resp = await client.post(f"/api/v1/connections/{created['id']}/test", headers=auth_a)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["error_category"] == "TIMEOUT"


async def test_f3_preview_timeout_returns_408(client, auth_a, container) -> None:
    created = await create_connection(client, auth_a)
    container.settings.preview_timeout_s = 0.05
    container.previewer = FakeSourcePreviewer(delay_s=1.0)
    resp = await client.post(
        f"/api/v1/connections/{created['id']}/preview", json={"table": "t"}, headers=auth_a
    )
    assert resp.status_code == 408
    assert resp.json()["error"]["code"] == "TIMEOUT"


async def test_f3_query_timeout_fails_job_with_timeout_category(client, auth_a, container) -> None:
    container.settings.query_timeout_s = 0.05
    container.query_sources.set("postgres", FakeQuerySource(rows=[{"id": 1}], delay_s=1.0))
    conn = await create_connection(client, auth_a)
    resp = await client.post(
        "/api/v1/ingestions",
        json={
            "ingestion_mode": "query",
            "connection_id": conn["id"],
            "statement": "SELECT * FROM slow_source",
            "new_dataset": {"name": "slow"},
        },
        headers=auth_a,
    )
    assert resp.status_code == 202
    job = resp.json()["data"]
    assert job["status"] == "failed"
    assert job["error_log"]["category"] == "TIMEOUT"
    assert container.table_writer.all_snapshots() == []  # nothing committed


# ------------------------------------------------------- F5 no orphaned secret


async def test_f5_name_conflict_leaves_no_orphaned_secret(client, auth_a, container) -> None:
    await create_connection(client, auth_a, name="Dup")  # first, password s3cr3t-pw
    resp = await client.post(
        "/api/v1/connections",
        json={
            **VALID_PG_CONNECTION,
            "name": "dup",  # case-insensitive clash -> 409
            "secrets": {"password": "would-be-orphan"},
        },
        headers=auth_a,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"
    # the losing request's secret was never written (F5)
    assert "would-be-orphan" not in container.secrets.dump_all_values()
    # exactly one connection, one stored secret
    listing = await client.get("/api/v1/connections", headers=auth_a)
    assert len(listing.json()["data"]) == 1
    assert container.secrets.dump_all_values() == ["s3cr3t-pw"]


async def test_f5_webhook_signing_secret_deferred_until_after_commit(
    container, principal_a
) -> None:
    """The endpoint helper must not touch the secrets store — the secret is a
    pending value the caller persists only after the row is durably committed."""
    svc = IngestionService(container)
    async with container.db.tenant_session(TENANT_A) as session:
        ing = Ingestion(
            id=uuid7(),
            tenant_id=TENANT_A,
            workspace_id=WORKSPACE,
            ingestion_mode="webhook_batch",
        )
        session.add(ing)
        await session.flush()
        info, pending = svc._create_webhook_endpoint(session, principal_a, ing)
        # secret NOT written yet — no orphan if the surrounding commit fails
        assert container.secrets.dump_all_values() == []
        vault_ref, secret_data = pending
        assert secret_data["signing_secret"] == info["signing_secret"]
        assert ing.id in vault_ref
