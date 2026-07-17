"""Event bus: real Kafka (Redpanda) via windrose_common + an in-memory double."""

from __future__ import annotations


class InMemoryEventBus:
    """Unit-tier double. Never wired from app.main."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, envelope: dict) -> None:
        self.published.append((topic, envelope))

    def of_type(self, event_type: str) -> list[dict]:
        return [e for _, e in self.published if e.get("event_type") == event_type]


class KafkaEventBus:
    """Real Kafka event bus (idempotent producer, keyed by tenant_id)."""

    def __init__(self, bootstrap_servers: str = "localhost:9092") -> None:
        from windrose_common.kafka import (
            KafkaConfig,
            KafkaProducerClient,
        )
        from windrose_common.kafka import (
            KafkaEventBus as _Bus,
        )

        self._client = KafkaProducerClient(KafkaConfig(bootstrap_servers=bootstrap_servers))
        self._bus = _Bus(self._client)
        self._started = False

    @property
    def producer(self):
        return self._client

    async def publish(self, topic: str, envelope: dict) -> None:
        if not self._started:
            await self._client.start()
            self._started = True
        await self._bus.publish(topic, envelope)

    async def aclose(self) -> None:
        if self._started:
            await self._client.stop()
            self._started = False
