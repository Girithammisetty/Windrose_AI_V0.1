"""Consumers for pipeline.events.v1 (run lifecycle), dataset.events.v1
(dataset.deleted -> flag cards), and identity.events.v1 (tenant.provisioned).

Handlers are idempotent and replay-safe (MASTER-FR-032). The Kafka runner
(Redis dedup, 5-retry backoff, real DLQ) comes from windrose_common.
"""

from __future__ import annotations

import logging

from app.domain.services import (
    CallCtx,
    CardService,
    RunService,
)

logger = logging.getLogger(__name__)

PIPELINE_TOPIC = "pipeline.events.v1"
DATASET_TOPIC = "dataset.events.v1"
IDENTITY_TOPIC = "identity.events.v1"


class PipelineEventHandler:
    """Drives run creation + status transitions from pipeline-orchestrator."""

    def __init__(self, run_service: RunService, dedup):
        self.runs = run_service
        self.dedup = dedup

    def _ctx(self, envelope: dict) -> CallCtx:
        return CallCtx(
            tenant_id=envelope["tenant_id"],
            actor=envelope.get("actor") or {"type": "service", "id": "pipeline-orchestrator"},
            via_agent=envelope.get("via_agent"), trace_id=envelope.get("trace_id"))

    async def handle(self, envelope: dict) -> None:
        tenant_id = envelope["tenant_id"]
        event_id = envelope.get("event_id", "")
        if event_id and await self.dedup.already_processed(tenant_id, event_id):
            return
        await self._dispatch(envelope)
        if event_id:
            await self.dedup.mark_processed(tenant_id, event_id)

    async def _dispatch(self, envelope: dict) -> None:
        ctx = self._ctx(envelope)
        event_type = envelope["event_type"]
        payload = envelope.get("payload", {})
        if event_type == "pipeline.run.submitted":
            await self.runs.create_from_pipeline(ctx, payload)
        elif event_type in ("pipeline.run.started", "pipeline.run.succeeded",
                            "pipeline.run.failed", "pipeline.run.cancelled"):
            await self.runs.transition_status(ctx, event_type, payload)
        elif event_type == "pipeline.run.output_registered":
            await self.runs.append_output_dataset(ctx, payload)


class KafkaPipelineConsumer:
    """Real aiokafka consumer group on pipeline.events.v1."""

    GROUP_ID = "experiment-service.pipeline"

    def __init__(self, handler: PipelineEventHandler, dedup, producer, *,
                 bootstrap_servers: str = "localhost:9092", topic: str = PIPELINE_TOPIC):
        from windrose_common.kafka import KafkaConfig, KafkaConsumer

        self._consumer = KafkaConsumer(
            topic, self.GROUP_ID, handler.handle, dedup, producer,
            cfg=KafkaConfig(bootstrap_servers=bootstrap_servers), max_retries=5)

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def consume_batch(self, max_messages: int, timeout_ms: int = 2000):
        return await self._consumer.consume_batch(max_messages, timeout_ms=timeout_ms)

    async def run(self, stop_event=None) -> None:
        await self._consumer.run(stop_event)


class DatasetEventHandler:
    """dataset.events.v1: dataset.deleted -> flag model cards referencing the
    dataset (training_data_unavailable=true). Idempotent + replay-safe
    (EXP-FR-040 / §6)."""

    def __init__(self, card_service: CardService, dedup):
        self.cards = card_service
        self.dedup = dedup

    async def handle(self, envelope: dict) -> None:
        tenant_id = envelope["tenant_id"]
        event_id = envelope.get("event_id", "")
        if event_id and await self.dedup.already_processed(tenant_id, event_id):
            return
        if envelope.get("event_type") == "dataset.deleted":
            dataset_urn = envelope.get("resource_urn") or (
                envelope.get("payload") or {}).get("dataset_urn")
            if dataset_urn:
                flagged = await self.cards.flag_dataset_deleted(tenant_id, dataset_urn)
                logger.info("dataset.deleted %s flagged %d model card(s)",
                            dataset_urn, flagged)
        if event_id:
            await self.dedup.mark_processed(tenant_id, event_id)


class KafkaDatasetConsumer:
    """Real aiokafka consumer group on dataset.events.v1."""

    GROUP_ID = "experiment-service.dataset"

    def __init__(self, handler: DatasetEventHandler, dedup, producer, *,
                 bootstrap_servers: str = "localhost:9092", topic: str = DATASET_TOPIC):
        from windrose_common.kafka import KafkaConfig, KafkaConsumer

        self._consumer = KafkaConsumer(
            topic, self.GROUP_ID, handler.handle, dedup, producer,
            cfg=KafkaConfig(bootstrap_servers=bootstrap_servers), max_retries=5)

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def consume_batch(self, max_messages: int, timeout_ms: int = 2000):
        return await self._consumer.consume_batch(max_messages, timeout_ms=timeout_ms)

    async def run(self, stop_event=None) -> None:
        await self._consumer.run(stop_event)
