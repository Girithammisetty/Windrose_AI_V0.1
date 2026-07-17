"""Consumers (BRD §6).

Flywheel case sourcing (EVL-FR-003) + streaming SLO rollups (EVL-FR-051):
* ``semantic.events.v1: verified_query.created|updated`` -> auto-active nl2sql case
* ``ai.proposal.v1: proposal.rejected`` -> candidate case (rejection reason label)
* ``ai.proposal.v1: proposal.edited_approved`` -> candidate case (edited args)
* ``ai.agent_run.v1`` / ``ai.token_usage.v1`` / ``ai.tool_invoked.v1`` /
  ``ai.proposal.v1`` -> SLO counters

Handlers are transport-agnostic and idempotent (event_id dedup); the real
aiokafka consumer-group runner (Redis dedup, retry/backoff, DLQ) drives
``handle`` in the runtime."""

from __future__ import annotations

import logging

from app.domain.entities import CallCtx
from app.domain.services import CaseService, SloService

logger = logging.getLogger(__name__)

SEMANTIC_TOPIC = "semantic.events.v1"
PROPOSAL_TOPIC = "ai.proposal.v1"
AGENT_RUN_TOPIC = "ai.agent_run.v1"
TOKEN_USAGE_TOPIC = "ai.token_usage.v1"
TOOL_TOPIC = "ai.tool_invoked.v1"

_SERVICE_ACTOR = {"type": "service", "id": "eval-service"}


class FlywheelHandler:
    def __init__(self, dedup, case_service: CaseService, slo_service: SloService):
        self.dedup = dedup
        self.cases = case_service
        self.slo = slo_service

    async def handle(self, envelope: dict) -> None:
        tenant_id = envelope.get("tenant_id", "")
        event_id = envelope.get("event_id", "")
        if event_id and await self.dedup.already_processed(tenant_id, event_id):
            return
        et = envelope.get("event_type", "")
        payload = envelope.get("payload", {})
        ctx = CallCtx(tenant_id=tenant_id, actor=_SERVICE_ACTOR, trace_id=envelope.get("trace_id"))
        try:
            if et in ("verified_query.created", "verified_query.updated"):
                await self.cases.from_verified_query(ctx, payload)
            elif et == "proposal.rejected":
                await self.cases.from_rejection(ctx, payload)
                await self.slo.ingest_event(
                    tenant_id,
                    payload.get("agent_key", "unknown"),
                    payload.get("agent_version"),
                    "proposal",
                    {**payload, "decision": "rejected"},
                )
            elif et in ("proposal.edited_approved", "proposal.approved"):
                if et == "proposal.edited_approved":
                    await self.cases.from_edit_diff(ctx, payload)
                await self.slo.ingest_event(
                    tenant_id,
                    payload.get("agent_key", "unknown"),
                    payload.get("agent_version"),
                    "proposal",
                    {**payload, "decision": et.split(".")[1]},
                )
            elif (
                et in ("agent_run.completed", "agent_run.failed", "agent_run.updated")
                or "agent_run" in et
            ):
                await self.slo.ingest_event(
                    tenant_id,
                    payload.get("agent_key", "unknown"),
                    payload.get("agent_version"),
                    "agent_run",
                    payload,
                )
            elif "token_usage" in et or et == "token.usage":
                await self.slo.ingest_event(
                    tenant_id,
                    payload.get("agent_key", "unknown"),
                    payload.get("agent_version"),
                    "token_usage",
                    payload,
                )
            elif "tool_invoked" in et or et == "tool.invoked":
                await self.slo.ingest_event(
                    tenant_id,
                    payload.get("agent_key", "unknown"),
                    payload.get("agent_version"),
                    "tool",
                    payload,
                )
        finally:
            pass
        if event_id:
            await self.dedup.mark_processed(tenant_id, event_id)


class KafkaTopicConsumer:
    """Real aiokafka consumer group over one topic via ``windrose_common``:
    Redis dedup, retry/backoff, real DLQ (MASTER-FR-032/033). Runtime consumer."""

    def __init__(
        self,
        handler: FlywheelHandler,
        dedup,
        producer,
        *,
        topic: str,
        group_suffix: str,
        bootstrap_servers: str = "localhost:9092",
    ):
        from windrose_common.kafka import KafkaConfig, KafkaConsumer

        self._consumer = KafkaConsumer(
            topic,
            f"eval-service.{group_suffix}",
            handler.handle,
            dedup,
            producer,
            cfg=KafkaConfig(bootstrap_servers=bootstrap_servers),
            max_retries=5,
        )

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def consume_batch(self, max_messages: int, timeout_ms: int = 2000):
        return await self._consumer.consume_batch(max_messages, timeout_ms=timeout_ms)

    async def run(self, stop_event=None) -> None:
        await self._consumer.run(stop_event)
