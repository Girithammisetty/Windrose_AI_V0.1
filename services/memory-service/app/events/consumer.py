"""Kafka consumers (MEM §6 consumed events).

A single transport-agnostic ``MemoryEventConsumer.handle(envelope)`` dispatches
by event_type; the real path drives it from one ``KafkaMemoryConsumer`` per
subscribed topic (windrose_common.KafkaConsumer: Redis dedup, retry/backoff,
DLQ). The in-memory bus dispatches it directly for the unit tier.
"""

from __future__ import annotations

from app.domain.entities import ErasureRequest
from app.domain.ports import CallCtx
from app.domain.services import (
    TOPIC,
    CorpusService,
    ErasureService,
    ProvisioningService,
    SessionService,
    ensure_tenant_provisioned,
)
from app.events.envelope import make_envelope
from app.utils import new_id

IDENTITY_TOPIC = "identity.events.v1"
CASE_TOPIC = "case.events.v1"
CHART_TOPIC = "chart.events.v1"
DATASET_TOPIC = "dataset.events.v1"
SEMANTIC_TOPIC = "semantic.events.v1"
AGENT_TOPIC = "agent.events.v1"
SECURITY_TOPIC = "security.events.v1"

CONSUMED_TOPICS = [
    IDENTITY_TOPIC, CASE_TOPIC, CHART_TOPIC, DATASET_TOPIC,
    SEMANTIC_TOPIC, AGENT_TOPIC, SECURITY_TOPIC,
]

_SYSTEM_ACTOR = {"type": "service", "id": "memory-service"}


class MemoryEventConsumer:
    def __init__(self, deps, dedup):
        self.d = deps
        self.dedup = dedup
        self.provisioning = ProvisioningService(deps)
        self.corpus = CorpusService(deps)
        self.erasure = ErasureService(deps)
        self.sessions = SessionService(deps)

    def _ctx(self, tenant_id: str, trace_id: str | None = None) -> CallCtx:
        return CallCtx(tenant_id=tenant_id, actor=_SYSTEM_ACTOR, trace_id=trace_id)

    async def handle(self, env: dict) -> None:
        tenant_id = env.get("tenant_id", "")
        event_id = env.get("event_id", "")
        if event_id and await self.dedup.seen(tenant_id, event_id):
            return
        et = env.get("event_type", "")
        if et == "tenant.provisioned":
            await self.provisioning.provision(tenant_id)
            self.d.provisioned_tenants.add(tenant_id)
            return
        if et == "tenant.deleted":
            await self.d.store.drop_tenant(tenant_id)
            self.d.provisioned_tenants.discard(tenant_id)
            return
        # Every other handler touches the tenant schema (memories/rag_chunks)
        # or expects the standard corpora rows — ensure the tenant is
        # provisioned first (BR-14 fallback for tenants whose
        # tenant.provisioned event predates the running consumers).
        if tenant_id:
            await ensure_tenant_provisioned(self.d, tenant_id)
        if et == "user.deleted":
            sid = env.get("payload", {}).get("user_id") or env.get("payload", {}).get("subject_id")
            if sid:
                req = ErasureRequest(
                    request_id=new_id(), tenant_id=tenant_id, subject_type="user",
                    subject_id=sid, status="received",
                    workflow_id=f"erasure-{tenant_id}-{sid}",
                    created_at=self.d.clock.now())
                await self.d.store.add_erasure(req)
                await self.erasure.run_sync(self._ctx(tenant_id), req)
        elif et in ("dataset.profiled", "dashboard.updated", "case.resolved"):
            await self.corpus.ingest_event(tenant_id, env)
        elif et in ("session.terminated", "session.expired"):
            sess = env.get("payload", {}).get("session_id")
            if sess:
                await self.sessions.wipe(tenant_id, sess)
        elif et == "run.flagged":
            run_id = env.get("payload", {}).get("run_id")
            if run_id:
                n = await self.d.store.quarantine_by_run(
                    tenant_id, run_id, self.d.clock.now())
                if n:
                    await self.d.store.add_outbox(tenant_id, TOPIC, make_envelope(
                        event_type="memory.quarantined", tenant_id=tenant_id,
                        actor=_SYSTEM_ACTOR,
                        resource_urn=f"wr:{tenant_id}:memory:run/{run_id}",
                        payload={"run_id": run_id, "quarantined": n,
                                 "reason": "run_flagged"}))


class KafkaMemoryConsumer:
    """Real Kafka consumer-group runner for one topic (windrose_common)."""

    def __init__(self, topic: str, consumer: MemoryEventConsumer, producer,
                 *, group_id: str | None = None, bootstrap_servers: str = "localhost:9092"):
        from windrose_common.kafka import KafkaConfig, KafkaConsumer

        self._runner = KafkaConsumer(
            topic, group_id or f"memory-service.{topic}", consumer.handle,
            _DedupAdapter(consumer.dedup), producer,
            cfg=KafkaConfig(bootstrap_servers=bootstrap_servers),
        )

    async def start(self):
        await self._runner.start()

    async def stop(self):
        await self._runner.stop()

    async def consume_batch(self, max_messages: int, timeout_ms: int = 4000):
        return await self._runner.consume_batch(max_messages, timeout_ms)


class _DedupAdapter:
    """windrose_common.KafkaConsumer expects already_processed/mark_processed;
    the MemoryEventConsumer.handle already dedups, so this is a no-op pass-through
    that lets the runner own commit semantics without double-suppressing."""

    def __init__(self, dedup):
        self._dedup = dedup

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        return False

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        return None
