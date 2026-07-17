"""Consumer for `ingestion.events.v1` (BRD §6).

ingestion.completed -> create/advance dataset + version, auto lineage edge,
trigger profiling. Idempotent twice over: event_id dedup (MASTER-FR-032) and
ingestion_id natural idempotency (produced_by_urn lookup).

The Kafka consumer-group adapter (Avro deserialization, 5-retry backoff, DLQ
routing per MASTER-FR-033) is stubbed below; handlers are transport-agnostic.
"""

from __future__ import annotations

import logging

from app.domain.entities import DatasetStatus, Visibility
from app.domain.errors import SnapshotAlreadyRegistered
from app.domain.ports import DedupStore
from app.domain.services import (
    CallCtx,
    DatasetService,
    LineageService,
    ServiceDeps,
    VersionService,
)
from app.domain.state import transition_dataset
from app.domain.urn import dataset_urn, parse_urn, version_urn
from app.events.envelope import make_envelope

logger = logging.getLogger(__name__)

INGESTION_TOPIC = "ingestion.events.v1"


class IngestionEventHandler:
    def __init__(
        self,
        deps: ServiceDeps,
        dedup: DedupStore,
        dataset_service: DatasetService,
        version_service: VersionService,
        lineage_service: LineageService,
    ):
        self.deps = deps
        self.dedup = dedup
        self.datasets = dataset_service
        self.versions = version_service
        self.lineage = lineage_service

    async def handle(self, envelope: dict) -> None:
        tenant_id = envelope["tenant_id"]
        event_id = envelope["event_id"]
        if await self.dedup.already_processed(tenant_id, event_id):
            logger.info("duplicate event %s skipped", event_id)
            return
        event_type = envelope["event_type"]
        # Handle first; if the handler raises, the marker is never written and
        # the event is re-run on redelivery. The handler is idempotent (natural
        # dedup on ingestion_id + snapshot + edge upsert), so re-runs never
        # double effects — exactly-once effect, DLQ-ready (MASTER-FR-032/033).
        if event_type == "ingestion.completed":
            await self._on_completed(envelope)
        elif event_type == "ingestion.failed":
            await self._on_failed(envelope)
        await self.dedup.mark_processed(tenant_id, event_id)

    def _ctx(self, envelope: dict) -> CallCtx:
        return CallCtx(
            tenant_id=envelope["tenant_id"],
            actor=envelope.get("actor") or {"type": "service", "id": "ingestion-service"},
            via_agent=envelope.get("via_agent"),
            trace_id=envelope.get("trace_id"),
        )

    async def _on_completed(self, envelope: dict) -> None:
        ctx = self._ctx(envelope)
        payload = envelope["payload"]
        ingestion_id = payload["ingestion_id"]
        ingestion_urn = f"wr:{ctx.tenant_id}:ingestion:ingestion/{ingestion_id}"

        # Guard: an event that carries neither an existing dataset reference nor the
        # workspace needed to create one cannot be auto-registered (e.g. a legacy
        # event predating the enriched ingestion.completed payload). Skip it
        # cleanly (mark processed) rather than raising into the retry/DLQ path.
        if not (self._event_dataset_id(ctx, payload) or payload.get("workspace_id")):
            logger.warning(
                "ingestion.completed %s missing dataset_id/workspace_id; skipping auto-register",
                ingestion_id,
            )
            return

        # Natural idempotency on ingestion_id: version already registered -> no-op
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            if await uow.versions.by_produced_by(ingestion_urn):
                logger.info("ingestion %s already registered; skipping", ingestion_id)
                return

        dataset = await self._resolve_dataset(ctx, payload)
        if dataset is None:
            logger.warning(
                "ingestion.completed %s references an unknown dataset and carries no "
                "workspace_id to create it; skipping auto-register",
                ingestion_id,
            )
            return
        try:
            version = await self.versions.register(
                ctx,
                dataset.id,
                {
                    "iceberg_snapshot_id": payload["iceberg_snapshot_id"],
                    "schema": payload.get("schema") or {},
                    "row_count": payload.get("row_count"),
                    "bytes": payload.get("bytes"),
                    "produced_by_urn": ingestion_urn,
                    "skip_profiling": payload.get("skip_profiling", False),
                },
            )
        except SnapshotAlreadyRegistered:
            # A concurrent delivery already registered this snapshot: safe skip.
            # A BR-1 'snapshot not yet readable' Conflict is NOT caught here — it
            # propagates so the event is left un-marked and retried on redelivery.
            logger.info("snapshot for ingestion %s already registered", ingestion_id)
            return

        # Automatic lineage edge (DST-FR-044): connection|upload -[ingested]-> version
        source_urn = payload.get("connection_urn") or (
            f"wr:{ctx.tenant_id}:ingestion:upload/{ingestion_id}"
        )
        await self.lineage.add_edge(
            ctx,
            {
                "from_urn": source_urn,
                "to_urn": version_urn(ctx.tenant_id, dataset.id, version.version_no),
                "activity": "ingested",
                "run_urn": ingestion_urn,
            },
        )

    @staticmethod
    def _event_dataset_id(ctx: CallCtx, payload: dict) -> str | None:
        """The dataset id ingestion-service minted (or was given), from the
        explicit ``dataset_id`` field or parsed out of ``dataset_urn``. This id
        is the SINGLE SOURCE OF TRUTH for the dataset row: every consumer that
        stored the ingestion's dataset_urn (case rows, lineage, the UI) must
        resolve to the row this handler creates. A URN whose tenant does not
        match the envelope tenant is ignored (defense in depth, MASTER-FR-003)."""
        if payload.get("dataset_id"):
            return str(payload["dataset_id"])
        raw_urn = payload.get("dataset_urn")
        if raw_urn:
            try:
                parsed = parse_urn(raw_urn)
            except Exception:  # noqa: BLE001 - malformed URN: fall through
                logger.warning("ignoring malformed dataset_urn %r", raw_urn)
                return None
            if (
                parsed.tenant == ctx.tenant_id
                and parsed.service == "dataset"
                and parsed.rtype == "dataset"
            ):
                return parsed.rid
            logger.warning("ignoring foreign/non-dataset dataset_urn %r", raw_urn)
        return None

    async def _resolve_dataset(self, ctx: CallCtx, payload: dict):
        dataset_id = self._event_dataset_id(ctx, payload)
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            if dataset_id:
                existing = await uow.datasets.get(dataset_id)
                if existing:
                    return existing
            if payload.get("dataset_name") and payload.get("workspace_id"):
                existing = await uow.datasets.get_by_name(
                    payload["workspace_id"], payload["dataset_name"]
                )
                if existing:
                    return existing
        if not payload.get("workspace_id"):
            return None  # cannot create without a workspace; caller skips cleanly
        # Auto-register UNDER THE EVENT'S ID: ingestion-service pre-minted the
        # dataset id (it is embedded in the bronze table name and in every
        # dataset_urn it handed out), so the row must carry that exact id —
        # minting a fresh one here is the URN drift this fixes.
        return await self.datasets.create(
            ctx,
            {
                "id": dataset_id,  # None -> DatasetService mints one (API path)
                "workspace_id": payload["workspace_id"],
                "name": payload.get("dataset_name") or f"ingestion-{payload['ingestion_id']}",
                "iceberg_table": payload.get("iceberg_table"),
                "visibility": Visibility.WORKSPACE,
                "tags": payload.get("tags") or [],
            },
        )

    async def _on_failed(self, envelope: dict) -> None:
        ctx = self._ctx(envelope)
        payload = envelope["payload"]
        dataset_id = payload.get("dataset_id")
        if not dataset_id:
            return
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            dataset = await uow.datasets.get(dataset_id)
            if not dataset:
                return
            versions = await uow.versions.list_all(dataset_id)
            if versions or dataset.status not in (
                DatasetStatus.DRAFT, DatasetStatus.PROCESSING
            ):
                return  # only fail datasets with no usable data (BRD §6)
            if dataset.status == DatasetStatus.DRAFT:
                transition_dataset(dataset, DatasetStatus.PROCESSING)
            transition_dataset(
                dataset,
                DatasetStatus.FAILED,
                error_log={"source": "ingestion", "digest": payload.get("error_digest")},
            )
            dataset.updated_at = self.deps.clock.now()
            await uow.datasets.update(dataset)
            await uow.outbox.add(
                self.deps.settings.events_topic,
                make_envelope(
                    event_type="dataset.updated",
                    tenant_id=ctx.tenant_id,
                    actor=ctx.actor,
                    via_agent=ctx.via_agent,
                    resource_urn=dataset_urn(ctx.tenant_id, dataset.id),
                    payload={"status": str(DatasetStatus.FAILED),
                             "error_digest": payload.get("error_digest")},
                    trace_id=ctx.trace_id,
                ),
            )
            await uow.commit()


class KafkaIngestionConsumer:
    """Real aiokafka consumer group (``dataset-service.ingestion``) on
    ingestion.events.v1 via the shared ``windrose_common`` consumer runner:
    Redis dedup, 5-retry exponential backoff and a real DLQ
    ``ingestion.events.v1.dataset-service.dlq`` (MASTER-FR-032/033). The
    transport-agnostic ``IngestionEventHandler.handle`` is the handler. Runtime
    consumer."""

    GROUP_ID = "dataset-service.ingestion"

    def __init__(
        self,
        handler: IngestionEventHandler,
        dedup,
        producer,
        *,
        bootstrap_servers: str = "localhost:9092",
        topic: str = INGESTION_TOPIC,
    ):
        from windrose_common.kafka import KafkaConfig, KafkaConsumer

        self._handler = handler
        self._consumer = KafkaConsumer(
            topic,
            self.GROUP_ID,
            handler.handle_raw if hasattr(handler, "handle_raw") else handler.handle,
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
