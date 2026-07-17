"""Transactional outbox on Postgres (MASTER-FR-034): state change and event
share one transaction — never emit before commit."""

from __future__ import annotations

import sqlalchemy as sa

from app.events.outbox import emit_event, publish_pending
from app.ids import uuid7
from app.store.models import Connection, OutboxEvent
from tests.util import TENANT_A, create_connection

WORKSPACE = "00000000-0000-0000-0000-000000000000"


async def test_mutation_and_event_commit_atomically(pg_client, auth_a, pg_app_container) -> None:
    created = await create_connection(pg_client, auth_a)
    async with pg_app_container.db.tenant_session(TENANT_A) as session:
        events = (
            (
                await session.execute(
                    sa.select(OutboxEvent).where(OutboxEvent.event_type == "connection.created")
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].payload["connection_id"] == created["id"]
    assert events[0].published_at is None  # emitted to outbox, not the broker


async def test_rollback_discards_both_row_and_event(pg_app_container) -> None:
    db = pg_app_container.db
    async with db.tenant_session(TENANT_A) as session:
        conn = Connection(
            id=uuid7(),
            tenant_id=TENANT_A,
            workspace_id=WORKSPACE,
            name="doomed",
            connector_type="postgres",
            config={"host": "h", "database": "d", "username": "u"},
            secret_field_names=[],
            tags=[],
        )
        session.add(conn)
        emit_event(
            session,
            tenant_id=TENANT_A,
            event_type="connection.created",
            resource_urn=f"wr:{TENANT_A}:ingestion:connection/{conn.id}",
            payload={"connection_id": conn.id},
        )
        await session.flush()
        await session.rollback()  # simulated failure before commit

    async with db.tenant_session(TENANT_A) as session:
        connections = (
            await session.execute(sa.select(sa.func.count()).select_from(Connection))
        ).scalar_one()
        events = (
            await session.execute(sa.select(sa.func.count()).select_from(OutboxEvent))
        ).scalar_one()
    assert connections == 0
    assert events == 0  # never emit before commit


async def test_publisher_poller_drains_in_order(pg_client, auth_a, pg_app_container) -> None:
    await create_connection(pg_client, auth_a, name="first")
    await create_connection(pg_client, auth_a, name="second")
    async with pg_app_container.db.tenant_session(TENANT_A) as session:
        drained = await publish_pending(session, pg_app_container.publisher)
        assert drained >= 2
        remaining = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
            )
        ).scalar_one()
    assert remaining == 0
    published = [v for _t, _k, v in pg_app_container.publisher.published]
    created = [p for p in published if p["event_type"] == "connection.created"]
    assert len(created) == 2
    occurred = [p["occurred_at"] for p in published]
    assert occurred == sorted(occurred)  # oldest-first
