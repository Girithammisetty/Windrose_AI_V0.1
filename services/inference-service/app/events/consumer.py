"""Cross-context event consumers (BRD §6 consumed).

* ``PipelineEventHandler`` — the sole job status source: correlates
  ``pipeline.events.v1`` by ``pipeline_run_urn`` and drives the job state machine
  (started -> running, succeeded/output_registered -> finalizing -> succeeded,
  failed -> failed, cancelled -> cancelled). Idempotent + replay-safe (AC-12): a
  duplicate ``succeeded`` yields exactly one finalize (finalize guards on the
  existing output-version for the job).
* ``ExperimentEventHandler`` — model_version.promoted/archived: refresh
  stage-selector schedules, warn pinned schedules on archive (INF-FR-061).
* ``DatasetEventHandler`` — dataset.deleted: pause schedules pinned to that
  dataset (paused_reason=INPUT_DELETED).
* ``UsageEventHandler`` — budget.exhausted/restored: toggle the tenant budget gate.

Each handler is idempotent; the ``KafkaConsumer`` wrapper adds Redis dedup, retry
and a DLQ (MASTER-FR-032/033).
"""

from __future__ import annotations

import logging

from app.domain.enums import JobStatus
from app.domain.ports import ScoringResult
from app.domain.services import InferenceService
from app.domain.urn import parse, schedule_urn
from app.events.envelope import make_envelope
from app.utils import utcnow

logger = logging.getLogger(__name__)


class PipelineEventHandler:
    def __init__(self, inference: InferenceService):
        self.inference = inference

    async def handle(self, envelope: dict) -> None:
        event_type = envelope.get("event_type", "")
        payload = envelope.get("payload") or {}
        run_urn = payload.get("pipeline_run_urn") or envelope.get("resource_urn")
        tenant_id = envelope.get("tenant_id")
        if not run_urn or not tenant_id:
            return
        async with self.inference.deps.uow_factory(tenant_id) as uow:
            job = await uow.jobs.by_pipeline_run_urn(run_urn)
        if job is None:
            return  # not this service's run
        if event_type == "pipeline.run.started":
            await self.inference._transition(tenant_id, job.id, JobStatus.running,
                                             event="inference.job.started")
        elif event_type in ("pipeline.run.succeeded", "pipeline.run.output_registered"):
            result = ScoringResult(
                output_storage_uri=payload.get("output_storage_uri", ""),
                snapshot_id=str(payload.get("snapshot_id", "")),
                row_count=int(payload.get("row_count", 0)),
                prediction_columns=payload.get("prediction_columns", ["prediction"]),
            )
            await self.inference.on_run_succeeded(tenant_id, job.id, result)
        elif event_type == "pipeline.run.failed":
            await self.inference.on_run_failed(tenant_id, job.id, {
                "code": "PIPELINE_FAILED",
                "component_alias": payload.get("component_alias"),
                "message": payload.get("message", "pipeline run failed")})
        elif event_type == "pipeline.run.cancelled":
            await self.inference._confirm_cancel(tenant_id, job.id)


class ExperimentEventHandler:
    def __init__(self, inference: InferenceService):
        self.inference = inference
        self.deps = inference.deps

    async def handle(self, envelope: dict) -> None:
        event_type = envelope.get("event_type", "")
        payload = envelope.get("payload") or {}
        tenant_id = envelope.get("tenant_id")
        model_version_urn = payload.get("model_version_urn")
        if not tenant_id:
            return
        async with self.deps.uow_factory(tenant_id) as uow:
            page = await uow.schedules.list(200, None)
            for sch in page.items:
                if event_type == "experiment.events.v1:model_version.archived" or \
                        event_type == "model_version.archived":
                    if sch.model_version_urn and sch.model_version_urn == model_version_urn:
                        env = make_envelope(
                            event_type="inference.schedule.model_archived_warning",
                            tenant_id=tenant_id, actor={"type": "service", "id": "inference"},
                            resource_urn=schedule_urn(tenant_id, sch.id),
                            payload={"schedule_id": sch.id,
                                     "model_version_urn": model_version_urn})
                        await uow.outbox.add("inference.events.v1", env)
                elif event_type in ("model_version.promoted",
                                    "experiment.events.v1:model_version.promoted"):
                    if sch.stage_selector is not None and sch.enabled:
                        # refresh next-fire preview (resolution happens fresh at fire)
                        sch.updated_at = utcnow()
                        await uow.schedules.update(sch)


class DatasetEventHandler:
    def __init__(self, inference: InferenceService):
        self.deps = inference.deps

    async def handle(self, envelope: dict) -> None:
        event_type = envelope.get("event_type", "")
        if not event_type.endswith("dataset.deleted"):
            return
        payload = envelope.get("payload") or {}
        tenant_id = envelope.get("tenant_id")
        dataset_urn = payload.get("dataset_urn") or envelope.get("resource_urn")
        if not tenant_id or not dataset_urn:
            return
        async with self.deps.uow_factory(tenant_id) as uow:
            page = await uow.schedules.list(200, None)
            for sch in page.items:
                if sch.input_selector.get("dataset_urn") == dataset_urn and sch.enabled:
                    sch.enabled = False
                    sch.paused_reason = "INPUT_DELETED"
                    sch.next_fire_at = None
                    sch.updated_at = utcnow()
                    await uow.schedules.update(sch)
                    env = make_envelope(
                        event_type="inference.schedule.paused",
                        tenant_id=tenant_id, actor={"type": "service", "id": "inference"},
                        resource_urn=schedule_urn(tenant_id, sch.id),
                        payload={"schedule_id": sch.id, "name": sch.name, "enabled": False,
                                 "paused_reason": "INPUT_DELETED"})
                    await uow.outbox.add("inference.events.v1", env)


class UsageEventHandler:
    def __init__(self, budget_gate):
        self.budget_gate = budget_gate

    async def handle(self, envelope: dict) -> None:
        event_type = envelope.get("event_type", "")
        payload = envelope.get("payload") or {}
        tenant_id = envelope.get("tenant_id")
        meter = payload.get("meter")
        if not tenant_id or self.budget_gate is None:
            return
        if meter and meter != "inference_minutes":
            return
        if event_type.endswith("budget.exhausted"):
            await self.budget_gate.set_exhausted(tenant_id, True)
        elif event_type.endswith("budget.restored"):
            await self.budget_gate.set_exhausted(tenant_id, False)


def _model_id(urn: str) -> str | None:
    try:
        return parse(urn).resource_id
    except Exception:  # noqa: BLE001
        return None
