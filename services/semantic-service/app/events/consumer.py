"""Consumers (BRD 06 §6).

- dataset.events.v1 :: dataset.schema_changed / dataset.deleted -> recompute
  health of published versions binding that dataset (SEM-FR-008), re-validate
  approved verified queries (SEM-FR-043);
- chart.events.v1 :: chart.created/updated -> reverse index chart->measures
  (deprecation impact);
- rbac.events.v1 :: workspace.deleted -> soft-delete the workspace's models.

Handlers are transport-agnostic and idempotent (event_id dedup, MASTER-FR-032);
the Kafka consumer-group adapter (Avro, 5-retry backoff, DLQ) is stubbed.
"""

from __future__ import annotations

import logging

from app.domain.definition import broken_refs_for_schema_change, parse_definition
from app.domain.entities import ChartRef
from app.domain.ports import DedupStore
from app.domain.services import CallCtx, ServiceDeps, _Base
from app.domain.sqlguard import referenced_words
from app.domain.state import check_vq_transition
from app.domain.urn import model_urn, verified_query_urn

logger = logging.getLogger(__name__)

DATASET_TOPIC = "dataset.events.v1"
CHART_TOPIC = "chart.events.v1"
RBAC_TOPIC = "rbac.events.v1"


class SemanticEventConsumer(_Base):
    def __init__(self, deps: ServiceDeps, dedup: DedupStore):
        super().__init__(deps)
        self.dedup = dedup

    def _ctx(self, envelope: dict) -> CallCtx:
        return CallCtx(
            tenant_id=envelope["tenant_id"],
            actor=envelope.get("actor") or {"type": "service", "id": "semantic-service"},
            via_agent=envelope.get("via_agent"),
            trace_id=envelope.get("trace_id"),
        )

    async def handle(self, envelope: dict) -> None:
        tenant_id = envelope["tenant_id"]
        if await self.dedup.seen(tenant_id, envelope["event_id"]):
            logger.info("duplicate event %s skipped", envelope["event_id"])
            return
        event_type = envelope["event_type"]
        if event_type == "dataset.schema_changed":
            await self._on_schema_changed(envelope)
        elif event_type == "dataset.deleted":
            await self._on_dataset_deleted(envelope)
        elif event_type == "dataset.restored":
            await self._on_dataset_restored(envelope)
        elif event_type in ("chart.created", "chart.updated"):
            await self._on_chart_event(envelope)
        elif event_type == "workspace.deleted":
            await self._on_workspace_deleted(envelope)

    # -- dataset.schema_changed (SEM-FR-008, AC-7) ---------------------------

    async def _on_schema_changed(self, envelope: dict) -> None:
        ctx = self._ctx(envelope)
        payload = envelope["payload"]
        dataset_urn = payload.get("dataset_urn") or envelope.get("resource_urn")
        removed = set(payload.get("removed_columns") or [])
        retyped = set((payload.get("retyped_columns") or {}).keys())
        if not dataset_urn or not (removed | retyped):
            return
        await self._recompute_health(ctx, dataset_urn, removed, retyped)
        await self._revalidate_verified_queries(ctx, removed | retyped)

    async def _on_dataset_deleted(self, envelope: dict) -> None:
        ctx = self._ctx(envelope)
        payload = envelope["payload"]
        dataset_urn = payload.get("dataset_urn") or envelope.get("resource_urn")
        if not dataset_urn:
            return
        async with self.uow(ctx.tenant_id) as uow:
            for model in await uow.models.all_active():
                if not model.published_version_id:
                    continue
                version = await uow.versions.get_by_id(model.published_version_id)
                if version is None:
                    continue
                defn = parse_definition(version.definition)
                bound = [e.name for e in defn.entities.values()
                         if e.dataset_urn == dataset_urn]
                if not bound:
                    continue
                broken = [{"object_type": "entity", "name": name,
                           "columns": [], "reason": "dataset deleted"}
                          for name in bound]
                # every measure/dimension on those entities breaks
                for dim in defn.dimensions.values():
                    if dim.entity in bound:
                        broken.append({"object_type": "dimension", "name": dim.name,
                                       "columns": [], "reason": "dataset deleted"})
                for meas in defn.measures.values():
                    if meas.entity in bound:
                        broken.append({"object_type": "measure", "name": meas.name,
                                       "columns": [], "reason": "dataset deleted"})
                model.health = {"status": "broken", "broken_refs": broken}
                model.updated_at = self.clock.now()
                await uow.models.update(model)
                await self._emit(uow, ctx, "model.health_changed",
                                 model_urn(ctx.tenant_id, model.id),
                                 {"broken_refs": broken})
            await uow.commit()

    async def _on_dataset_restored(self, envelope: dict) -> None:
        """dataset.restored -> undoes the break _on_dataset_deleted recorded
        for entities bound to this dataset_urn (SEM-FR-008 inverse). Before
        this handler existed, dataset.restored was emitted by dataset-service
        but never consumed here, so archiving a dataset bound to a semantic
        model broke every measure/dimension on it PERMANENTLY -- restoring the
        dataset never healed the model. _on_dataset_deleted unconditionally
        OVERWRITES model.health with an all-reason="dataset deleted" break
        list, so a model whose CURRENT health is entirely that reason is safe
        to clear back to healthy: restore reinstates the exact prior dataset
        state, nothing about the schema actually changed. A model broken for
        any OTHER reason (a genuine dataset.schema_changed) is left alone --
        that reflects a real, separate problem restoring the dataset does
        not fix."""
        ctx = self._ctx(envelope)
        payload = envelope["payload"]
        dataset_urn = payload.get("dataset_urn") or envelope.get("resource_urn")
        if not dataset_urn:
            return
        async with self.uow(ctx.tenant_id) as uow:
            for model in await uow.models.all_active():
                if not model.published_version_id:
                    continue
                version = await uow.versions.get_by_id(model.published_version_id)
                if version is None:
                    continue
                defn = parse_definition(version.definition)
                if not any(e.dataset_urn == dataset_urn for e in defn.entities.values()):
                    continue
                health = model.health or {}
                refs = health.get("broken_refs") or []
                if health.get("status") != "broken" or not refs:
                    continue
                if not all(r.get("reason") == "dataset deleted" for r in refs):
                    continue  # broken for another reason too; leave it alone
                model.health = {"status": "ok", "broken_refs": []}
                model.updated_at = self.clock.now()
                await uow.models.update(model)
                await self._emit(uow, ctx, "model.health_changed",
                                 model_urn(ctx.tenant_id, model.id), {"broken_refs": []})
            await uow.commit()

    async def _recompute_health(self, ctx: CallCtx, dataset_urn: str,
                                removed: set[str], retyped: set[str]) -> None:
        async with self.uow(ctx.tenant_id) as uow:
            for model in await uow.models.all_active():
                if not model.published_version_id:
                    continue
                version = await uow.versions.get_by_id(model.published_version_id)
                if version is None:
                    continue
                defn = parse_definition(version.definition)
                if not any(e.dataset_urn == dataset_urn for e in defn.entities.values()):
                    continue
                broken = broken_refs_for_schema_change(defn, dataset_urn, removed, retyped)
                new_health = ({"status": "broken", "broken_refs": broken}
                              if broken else {"status": "ok", "broken_refs": []})
                if new_health != (model.health or {}):
                    model.health = new_health
                    model.updated_at = self.clock.now()
                    await uow.models.update(model)
                    await self._emit(uow, ctx, "model.health_changed",
                                     model_urn(ctx.tenant_id, model.id),
                                     {"broken_refs": broken})
            await uow.commit()

    async def _revalidate_verified_queries(self, ctx: CallCtx,
                                           columns: set[str]) -> None:
        """SEM-FR-043 / AC-11: approved queries touching changed columns ->
        pending_review with a health_note."""
        lowered = {c.lower() for c in columns}
        async with self.uow(ctx.tenant_id) as uow:
            for vq in await uow.verified_queries.approved_all():
                hit = sorted(lowered & referenced_words(vq.sql_text))
                if not hit:
                    continue
                check_vq_transition(vq.status, "pending_review")
                vq.status = "pending_review"
                vq.health_note = f"dataset schema change touched: {', '.join(hit)}"
                vq.updated_at = self.clock.now()
                await uow.verified_queries.update(vq)
                await self._emit(uow, ctx, "verified_query.submitted",
                                 verified_query_urn(ctx.tenant_id, vq.id),
                                 {"reason": "revalidation",
                                  "health_note": vq.health_note})
            await uow.commit()

    # -- chart.events.v1 reverse index ---------------------------------------

    async def _on_chart_event(self, envelope: dict) -> None:
        ctx = self._ctx(envelope)
        payload = envelope["payload"]
        chart_urn = envelope.get("resource_urn") or payload.get("chart_urn")
        if not chart_urn:
            return
        async with self.uow(ctx.tenant_id) as uow:
            await uow.chart_refs.upsert(ChartRef(
                tenant_id=ctx.tenant_id, chart_urn=chart_urn,
                model=payload.get("model"),
                measures=list(payload.get("measures") or []),
            ))
            await uow.commit()

    # -- rbac.events.v1 :: workspace.deleted ----------------------------------

    async def _on_workspace_deleted(self, envelope: dict) -> None:
        ctx = self._ctx(envelope)
        workspace_id = envelope["payload"].get("workspace_id")
        if not workspace_id:
            return
        async with self.uow(ctx.tenant_id) as uow:
            for model in await uow.models.all_active():
                if model.workspace_id != workspace_id:
                    continue
                model.deleted_at = self.clock.now()
                model.updated_at = model.deleted_at
                await uow.models.update(model)
                await self._emit(uow, ctx, "model.deleted",
                                 model_urn(ctx.tenant_id, model.id),
                                 {"name": model.name, "reason": "workspace.deleted"})
            await uow.commit()


class _PassthroughDedup:
    """No-op dedup for the transport-layer consumer: the
    ``SemanticEventConsumer.handle`` handler already dedups every event via its
    own Redis dedup store (``seen``), so it stays the single dedup authority and
    the transport runner never double-marks."""

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        return False

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        return None


class KafkaSemanticConsumer:
    """Real aiokafka consumer group (``semantic-service.<topic>``) via the shared
    ``windrose_common`` consumer runner: 5-retry exponential backoff and a real
    per-group DLQ ``<topic>.semantic-service.<topic>.dlq`` (MASTER-FR-033).
    Dedup (MASTER-FR-032) is owned by ``SemanticEventConsumer.handle`` (Redis),
    so the runner uses a passthrough dedup. One consumer per subscribed topic;
    ``SemanticEventConsumer.handle`` is the transport-agnostic handler. Runtime
    consumer."""

    def __init__(
        self,
        topic: str,
        handler: SemanticEventConsumer,
        producer,
        *,
        bootstrap_servers: str = "localhost:9092",
    ):
        from windrose_common.kafka import KafkaConfig, KafkaConsumer

        self.topic = topic
        self._handler = handler
        self._consumer = KafkaConsumer(
            topic,
            f"semantic-service.{topic}",
            handler.handle,
            _PassthroughDedup(),
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
