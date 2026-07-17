"""Event bus + consumer dedup implementations (vendored, wave-1)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable

Handler = Callable[[dict], Awaitable[None]]


class InMemoryEventBus:
    """In-memory fake of the Kafka bus: records published envelopes and
    dispatches to registered subscribers."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)

    async def publish(self, topic: str, envelope: dict) -> None:
        self.published.append((topic, envelope))
        for handler in self._subscribers[topic]:
            await handler(envelope)

    def events_of_type(self, event_type: str) -> list[dict]:
        return [e for _, e in self.published if e["event_type"] == event_type]

    def on_topic(self, topic: str) -> list[dict]:
        return [e for t, e in self.published if t == topic]


class KafkaEventBus:
    """Real Kafka (Redpanda) event bus over the shared ``windrose_common``
    idempotent producer. Publishes the MASTER §2.4 envelope keyed by
    ``tenant_id`` so a tenant's events stay ordered on one partition
    (MASTER-FR-030/031). The outbox dispatcher drives it from committed rows,
    so ``ai.token_usage.v1`` metering events land on real Redpanda. This is the
    runtime event bus wired by ``main.py``; the producer starts lazily on first
    publish."""

    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        from windrose_common.kafka import KafkaConfig, KafkaProducerClient
        from windrose_common.kafka import KafkaEventBus as _Bus

        self._client = KafkaProducerClient(KafkaConfig(bootstrap_servers=bootstrap_servers))
        self._bus = _Bus(self._client)
        self._started = False

    async def publish(self, topic: str, envelope: dict) -> None:
        if not self._started:
            await self._client.start()
            self._started = True
        await self._bus.publish(topic, envelope)

    async def aclose(self) -> None:
        if self._started:
            await self._client.stop()
            self._started = False


class InMemoryDedupStore:
    """SETNX-style dedup fake (MASTER-FR-032; Redis in prod)."""

    def __init__(self):
        self._seen: set[tuple[str, str]] = set()

    async def seen(self, tenant_id: str, event_id: str) -> bool:
        key = (tenant_id, event_id)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False


class RedisDedupStore:
    """Redis SETNX with 24h TTL per MASTER-FR-032."""

    def __init__(self, kv):
        self.kv = kv

    async def seen(self, tenant_id: str, event_id: str) -> bool:
        fresh = await self.kv.setnx(f"evt:{tenant_id}:{event_id}", "1",
                                    ttl_seconds=86_400)
        return not fresh
