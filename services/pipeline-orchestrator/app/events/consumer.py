"""Consumed events (BRD §6). A transport-agnostic ``PipelineEventConsumer.handle``
dispatches by event_type; the real path drives it from one ``KafkaPipelineConsumer``
per topic (windrose_common.KafkaConsumer: Redis dedup, retry/backoff, DLQ)."""

from __future__ import annotations

import logging

from app.domain.entities import TenantQuota
from app.domain.labeling import LabeledExampleAssembler

logger = logging.getLogger(__name__)

IDENTITY_TOPIC = "identity.events.v1"
CASE_TOPIC = "case.events.v1"
DATASET_TOPIC = "dataset.events.v1"
CONSUMED_TOPICS = [IDENTITY_TOPIC, CASE_TOPIC, DATASET_TOPIC]


class PipelineEventConsumer:
    def __init__(self, deps, dedup, *, feature_source=None, default_quota=None):
        self.d = deps
        self.dedup = dedup
        self.assembler = LabeledExampleAssembler(deps, feature_source=feature_source)
        self.default_quota = default_quota or {}

    async def handle(self, env: dict) -> None:
        tenant_id = env.get("tenant_id", "")
        event_id = env.get("event_id", "")
        if event_id and await self.dedup.already_processed(tenant_id, event_id):
            return
        et = env.get("event_type", "")
        try:
            if et == "tenant.provisioned":
                await self._provision(tenant_id)
            elif et in ("case.disposition_applied", "case.correction_recorded"):
                ex = await self.assembler.handle_disposition(env)
                if ex:
                    logger.info("labeled example assembled dataset=%s row=%s label=%s",
                                ex.dataset_urn, ex.row_pk, ex.label)
        finally:
            if event_id:
                await self.dedup.mark_processed(tenant_id, event_id)

    async def _provision(self, tenant_id: str) -> None:
        async with self.d.uow_factory(tenant_id) as uow:
            if await uow.quotas.get(tenant_id) is None:
                await uow.quotas.upsert(TenantQuota(
                    tenant_id=tenant_id,
                    max_concurrent_runs=self.default_quota.get("max_concurrent_runs", 10),
                    max_concurrent_pods=self.default_quota.get("max_concurrent_pods", 40),
                    max_run_duration_minutes=self.default_quota.get(
                        "max_run_duration_minutes", 480),
                    min_seconds_between_runs=self.default_quota.get(
                        "min_seconds_between_runs", 15)))


class _DedupPassthrough:
    """windrose_common.KafkaConsumer owns commit semantics; the handler already
    dedups, so this pass-through avoids double-suppression."""

    async def already_processed(self, tenant_id, event_id):
        return False

    async def mark_processed(self, tenant_id, event_id):
        return None


class KafkaPipelineConsumer:
    """Real Kafka consumer-group runner for one topic (windrose_common)."""

    def __init__(self, topic, consumer, producer, *, group_id=None,
                 bootstrap_servers="localhost:9092"):
        from windrose_common.kafka import KafkaConfig, KafkaConsumer

        self._runner = KafkaConsumer(
            topic, group_id or f"pipeline-orchestrator.{topic}", consumer.handle,
            _DedupPassthrough(), producer,
            cfg=KafkaConfig(bootstrap_servers=bootstrap_servers))

    async def start(self):
        await self._runner.start()

    async def stop(self):
        await self._runner.stop()

    async def consume_batch(self, max_messages, timeout_ms=6000):
        return await self._runner.consume_batch(max_messages, timeout_ms)

    async def run(self, stop_event=None):
        await self._runner.run(stop_event)
