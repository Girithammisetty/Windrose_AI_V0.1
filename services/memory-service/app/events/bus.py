"""Event bus + consumer dedup: in-memory doubles (unit) and real Kafka/Redis
(runtime), mirroring the semantic-service/dataset-service wiring."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable

Handler = Callable[[dict], Awaitable[None]]


class InMemoryEventBus:
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
        return [e for _, e in self.published if e.get("event_type") == event_type]


class KafkaEventBus:
    """Real Kafka (Redpanda) event bus via the shared windrose_common idempotent
    producer; publishes the master envelope keyed by tenant_id. Runtime bus."""

    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        from windrose_common.kafka import KafkaConfig, KafkaProducerClient
        from windrose_common.kafka import KafkaEventBus as _Bus

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


class InMemoryDedupStore:
    """SETNX-style dedup fake (MASTER-FR-032). Unit-tier double only."""

    def __init__(self):
        self._seen: set[tuple[str, str]] = set()

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        return (tenant_id, event_id) in self._seen

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        self._seen.add((tenant_id, event_id))

    async def seen(self, tenant_id: str, event_id: str) -> bool:
        key = (tenant_id, event_id)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False


class RedisDedupStore:
    """Real Redis consumer dedup (24h TTL) via shared windrose_common. Runtime."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        from windrose_common.redisx import RedisDedupStore as _Dedup
        from windrose_common.redisx import build_redis

        self._redis = build_redis(redis_url)
        self._store = _Dedup(self._redis)

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        return await self._store.already_processed(tenant_id, event_id)

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        await self._store.mark_processed(tenant_id, event_id)

    async def seen(self, tenant_id: str, event_id: str) -> bool:
        won = await self._store.claim(tenant_id, event_id)
        return not won

    async def aclose(self) -> None:
        await self._redis.aclose()
