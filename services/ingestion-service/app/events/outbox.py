"""Transactional outbox (MASTER-FR-030/031/034/035).

`emit_event` adds an outbox row to the CURRENT session/transaction — events
are never emitted before commit. `publish_pending` is the poller half
(Debezium replaces it in prod); InMemoryEventPublisher backs tests and
KafkaEventPublisher is the production stub.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.context import current_trace_id
from app.ids import uuid7
from app.store.models import OutboxEvent

TOPIC = "ingestion.events.v1"

SERVICE_ACTOR = {"type": "service", "id": "ingestion-service"}


def emit_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    event_type: str,
    resource_urn: str,
    payload: dict[str, Any],
    actor: dict[str, Any] | None = None,
    via_agent: dict[str, Any] | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        id=uuid7(),
        tenant_id=tenant_id,
        event_id=uuid7(),
        event_type=event_type,
        resource_urn=resource_urn,
        actor=actor or SERVICE_ACTOR,
        via_agent=via_agent,
        occurred_at=datetime.now(UTC),
        trace_id=current_trace_id(),
        payload=payload,
    )
    session.add(event)
    return event


def envelope(event: OutboxEvent) -> dict[str, Any]:
    """MASTER-FR-031 envelope; partition key = tenant_id."""
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "tenant_id": event.tenant_id,
        "actor": event.actor,
        "via_agent": event.via_agent,
        "resource_urn": event.resource_urn,
        "occurred_at": event.occurred_at.isoformat(),
        "trace_id": event.trace_id,
        "payload": event.payload,
    }


class EventPublisher(Protocol):
    async def publish(self, topic: str, key: str, value: dict[str, Any]) -> None: ...


class InMemoryEventPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict[str, Any]]] = []

    async def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        self.published.append((topic, key, value))


class KafkaEventPublisher:
    """Real Kafka (Redpanda) publisher via the shared ``windrose_common``
    idempotent aiokafka producer. Keyed by tenant_id so a tenant's events keep a
    single-partition order (MASTER-FR-031). Runtime event publisher; the outbox
    relay drives it from committed rows."""

    def __init__(self, bootstrap_servers: str = "localhost:9092") -> None:
        from windrose_common.kafka import KafkaConfig, KafkaProducerClient

        self.bootstrap_servers = bootstrap_servers
        self._client = KafkaProducerClient(KafkaConfig(bootstrap_servers=bootstrap_servers))
        self._started = False

    async def _ensure_started(self) -> None:
        if not self._started:
            await self._client.start()
            self._started = True

    async def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        await self._ensure_started()
        await self._client.send(topic, key, value)

    async def aclose(self) -> None:
        if self._started:
            await self._client.stop()
            self._started = False


async def publish_pending(
    session: AsyncSession, publisher: EventPublisher, limit: int = 100
) -> int:
    """Poller: publish committed outbox rows oldest-first, mark published_at.

    Drains across every tenant, so it cannot rely on a per-request
    `tenant_session` (app.tenant_id GUC). On Postgres, the runtime role is
    NOSUPERUSER NOBYPASSRLS (migration 0004), so a plain SELECT would hit the
    `tenant_isolation` policy with no GUC set. Instead this claims/marks rows
    through the `ing_outbox_claim_pending`/`ing_outbox_mark_published`
    SECURITY DEFINER functions (migration 0005) -- an RLS bypass scoped to
    just the outbox table. SQLite (unit tier, no RLS) uses a plain query.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        rows = (
            (
                await session.execute(
                    sa.select(OutboxEvent).from_statement(
                        sa.text("SELECT * FROM ing_outbox_claim_pending(:lim)")
                    ),
                    {"lim": limit},
                )
            )
            .scalars()
            .all()
        )
    else:
        rows = (
            (
                await session.execute(
                    sa.select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.occurred_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        return 0
    for event in rows:
        await publisher.publish(TOPIC, event.tenant_id, envelope(event))
    if dialect == "postgresql":
        await session.execute(
            sa.text("SELECT ing_outbox_mark_published(CAST(:ids AS uuid[]))"),
            {"ids": [event.id for event in rows]},
        )
    else:
        for event in rows:
            event.published_at = datetime.now(UTC)
    await session.commit()
    return len(rows)
