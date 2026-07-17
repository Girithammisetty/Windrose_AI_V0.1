"""Real transactional-outbox relay: polls a service's ``outbox`` table and
publishes committed rows to Kafka, then marks them published — never emitting an
event before its state change commits (MASTER-FR-034).

The relay is table-shape agnostic (configured by ``OutboxTableSpec``) so it drives
both the ingestion-service and dataset-service outbox tables. It selects
unpublished rows ``FOR UPDATE SKIP LOCKED`` so multiple relay workers can run
concurrently without double-publishing.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from .kafka import KafkaProducerClient


@dataclass(slots=True)
class OutboxTableSpec:
    table: str = "outbox"
    id_col: str = "id"
    topic_col: str | None = None  # if None, use default_topic
    default_topic: str | None = None
    key_col: str = "tenant_id"
    payload_col: str = "payload"
    published_col: str = "published_at"
    order_col: str = "occurred_at"
    worker_guc: str | None = None  # e.g. "app.worker" for cross-tenant read policy


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        producer: KafkaProducerClient,
        spec: OutboxTableSpec | None = None,
        *,
        batch_size: int = 100,
    ) -> None:
        self._session_factory = session_factory
        self._producer = producer
        self.spec = spec or OutboxTableSpec()
        self._batch = batch_size

    async def relay_once(self) -> int:
        s = self.spec
        cols = f'{s.id_col}, {s.key_col}, {s.payload_col}'
        if s.topic_col:
            cols += f', {s.topic_col}'
        select_sql = sa.text(
            f'SELECT {cols} FROM {s.table} '
            f'WHERE {s.published_col} IS NULL '
            f'ORDER BY {s.order_col} ASC '
            f'LIMIT :limit FOR UPDATE SKIP LOCKED'
        )
        async with self._session_factory() as session:
            if s.worker_guc:
                await session.execute(
                    sa.text("SELECT set_config(:g, 'true', true)"), {"g": s.worker_guc}
                )
            rows = (await session.execute(select_sql, {"limit": self._batch})).mappings().all()
            published_ids = []
            for row in rows:
                topic = row[s.topic_col] if s.topic_col else s.default_topic
                if not topic:
                    raise ValueError("no topic column and no default_topic configured")
                key = row[s.key_col]
                value = row[s.payload_col]
                await self._producer.send(topic, str(key) if key is not None else None, value)
                published_ids.append(row[s.id_col])
            if published_ids:
                await session.execute(
                    sa.text(
                        f'UPDATE {s.table} SET {s.published_col} = now() '
                        f'WHERE {s.id_col} = ANY(:ids)'
                    ),
                    {"ids": published_ids},
                )
            await session.commit()
            return len(rows)
