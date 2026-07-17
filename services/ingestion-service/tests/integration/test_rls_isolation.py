"""RLS isolation suite (MASTER-FR-001/003/004): enforced by Postgres policies
through the non-superuser app role — no explicit tenant filters involved."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.ids import uuid7
from app.store.models import Connection, Ingestion, OutboxEvent, Schedule, Upload
from tests.util import TENANT_A, TENANT_B, create_connection, outbox_events

WORKSPACE = "00000000-0000-0000-0000-000000000000"


def _connection(tenant: str) -> Connection:
    return Connection(
        id=uuid7(),
        tenant_id=tenant,
        workspace_id=WORKSPACE,
        name=f"conn-{uuid7()[:8]}",
        connector_type="postgres",
        config={"host": "h", "database": "d", "username": "u"},
        secret_field_names=[],
        tags=[],
    )


async def test_rls_hides_other_tenants_rows_without_filters(pg_app_container) -> None:
    db = pg_app_container.db
    async with db.tenant_session(TENANT_A) as session:
        conn_a = _connection(TENANT_A)
        session.add(conn_a)
        session.add(
            Ingestion(
                id=uuid7(), tenant_id=TENANT_A, workspace_id=WORKSPACE, ingestion_mode="query"
            )
        )
        session.add(
            OutboxEvent(
                id=uuid7(),
                tenant_id=TENANT_A,
                event_id=uuid7(),
                event_type="x",
                resource_urn="urn",
                actor={"type": "user", "id": "a"},
                payload={},
            )
        )
        await session.commit()

    # tenant B sees NOTHING — raw selects, no tenant filters anywhere
    async with db.tenant_session(TENANT_B) as session:
        for table in ("connections", "ingestions", "outbox"):
            count = (await session.execute(sa.text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0, f"tenant B can see {table} rows of tenant A"

    async with db.tenant_session(TENANT_A) as session:
        count = (await session.execute(sa.text("SELECT count(*) FROM connections"))).scalar_one()
        assert count == 1


async def test_rls_with_check_blocks_cross_tenant_writes(pg_app_container) -> None:
    db = pg_app_container.db
    async with db.tenant_session(TENANT_A) as session:
        session.add(_connection(TENANT_B))  # wrong tenant in payload
        with pytest.raises((DBAPIError, ProgrammingError)):
            await session.commit()


async def test_rls_default_deny_without_tenant_context(pg_app_container) -> None:
    """No app.tenant_id set -> current_setting errors -> zero access."""
    async with pg_app_container.db.session_factory() as session:
        with pytest.raises((DBAPIError, ProgrammingError)):
            await session.execute(sa.text("SELECT count(*) FROM connections"))


async def test_rls_applies_to_all_service_tables(pg_app_container) -> None:
    db = pg_app_container.db
    async with db.tenant_session(TENANT_A) as session:
        conn = _connection(TENANT_A)
        session.add(conn)
        ing = Ingestion(
            id=uuid7(), tenant_id=TENANT_A, workspace_id=WORKSPACE, ingestion_mode="file_upload"
        )
        session.add(ing)
        await session.flush()
        session.add(
            Upload(
                id=uuid7(),
                tenant_id=TENANT_A,
                ingestion_id=ing.id,
                part_size=1024,
                storage_prefix="p",
                expires_at=sa.func.now(),
            )
        )
        session.add(
            Schedule(
                id=uuid7(),
                tenant_id=TENANT_A,
                workspace_id=WORKSPACE,
                connection_id=conn.id,
                ingestion_template={"ingestion_mode": "query", "statement": "SELECT 1"},
                timezone="UTC",
                temporal_schedule_id="inproc-x",
            )
        )
        await session.commit()

    async with db.tenant_session(TENANT_B) as session:
        for table in ("uploads", "schedules", "ingestion_transitions", "idempotency_keys"):
            count = (await session.execute(sa.text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0


async def test_api_cross_tenant_read_is_404_under_rls(pg_client, auth_a, auth_b) -> None:
    """MASTER-FR-004: every endpoint denies tenant A access to tenant B data."""
    created = await create_connection(pg_client, auth_b)
    resp = await pg_client.get(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert resp.status_code == 404
    resp = await pg_client.patch(
        f"/api/v1/connections/{created['id']}", json={"tags": ["x"]}, headers=auth_a
    )
    assert resp.status_code == 404
    resp = await pg_client.delete(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert resp.status_code == 404
    resp = await pg_client.post(f"/api/v1/connections/{created['id']}/test", headers=auth_a)
    assert resp.status_code == 404
    listing = await pg_client.get("/api/v1/connections", headers=auth_a)
    assert listing.json()["data"] == []


async def test_f2_cross_tenant_denied_audit_fires_under_rls(
    pg_client, auth_a, auth_b, pg_app_container
) -> None:
    """F2 / AC-3 / MASTER-FR-003: the audit event must fire under the
    non-superuser app_rls role even though tenant B's row is invisible to A."""
    created = await create_connection(pg_client, auth_b)  # tenant B's connection
    resp = await pg_client.get(f"/api/v1/connections/{created['id']}", headers=auth_a)
    assert resp.status_code == 404

    events = await outbox_events(pg_app_container, TENANT_A, "security.cross_tenant_denied")
    assert len(events) == 1
    assert events[0]["payload"]["resource_id"] == created["id"]
    assert events[0]["payload"]["resource_type"] == "connection"

    # a genuinely non-existent id (owned by nobody) must NOT emit the audit event
    resp = await pg_client.get(f"/api/v1/connections/{uuid7()}", headers=auth_a)
    assert resp.status_code == 404
    events = await outbox_events(pg_app_container, TENANT_A, "security.cross_tenant_denied")
    assert len(events) == 1  # unchanged — no false positive
