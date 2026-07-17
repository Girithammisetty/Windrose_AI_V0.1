"""Event bus adapters. Services never publish directly — they append to the
transactional outbox; the OutboxDispatcher relays committed rows to this bus.
Real mode uses Kafka (Redpanda); the in-memory bus is unit-tier only."""

from __future__ import annotations


class KafkaOutboxBus:
    """publish(topic, envelope) over the shared idempotent Kafka producer, keyed by
    tenant_id so a tenant's events stay ordered on one partition (MASTER-FR-031)."""

    def __init__(self, producer):
        self._producer = producer

    async def publish(self, topic: str, envelope: dict) -> None:
        await self._producer.send(topic, envelope.get("tenant_id"), envelope)
