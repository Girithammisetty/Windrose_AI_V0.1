"""Inference domain services: job submission/validation/lifecycle + schedules.

The scoring *run* is performed by a real local executor (loads the model from
MLflow, predicts on the real input data, writes a single-snapshot output parquet
to object storage). That executor is the local real substitute for the
pipeline-orchestrator/Argo run; it drives the same idempotent state transitions
that the ``pipeline.events.v1`` consumer drives, so the state machine is event-
faithful and replay-safe (AC-12).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.entities import InferenceJob, LineageEdge
from app.domain.enums import (
    CANCELLABLE,
    TERMINAL_FAILURE,
    JobStatus,
    ModelStage,
    OutputMode,
    status_name,
)
from app.domain.errors import (
    Conflict,
    DependencyUnavailable,
    ModelStageDenied,
    NotFound,
    PermissionDenied,
    RateLimited,
    ValidationFailed,
)
from app.domain.ports import (
    CallCtx,
    Filters,
    ResolvedDataset,
    ResolvedModel,
    ScoringResult,
    ServiceDeps,
)
from app.domain.schema_compat import CompatibilityReport, validate_compatibility
from app.domain.state import can_transition
from app.domain.urn import job_urn, parse
from app.utils import utcnow, uuid7

_NAME_RE = re.compile(r"^[a-zA-Z0-9_\- ]{3,120}$")
EVENTS_TOPIC = "inference.events.v1"


def make_envelope(*, event_type: str, ctx: CallCtx, resource_urn: str, payload: dict) -> dict:
    return {
        "event_id": str(uuid7()),
        "event_type": event_type,
        "tenant_id": ctx.tenant_id,
        "actor": ctx.actor,
        "via_agent": ctx.via_agent,
        "resource_urn": resource_urn,
        "occurred_at": utcnow().isoformat(),
        "trace_id": ctx.trace_id,
        "payload": payload,
    }


@dataclass
class SubmitRequest:
    model_version_urn: str
    input_dataset_urn: str
    name: str | None = None
    description: str | None = None
    parameters: dict | None = None
    output: dict | None = None
    allow_unpromoted: bool = False
    allow_empty: bool = False
    schedule_id: str | None = None


class _ResolveMixin:
    deps: ServiceDeps

    async def _resolve_model(self, ctx: CallCtx, model_version_urn: str) -> ResolvedModel:
        parsed = parse(model_version_urn)
        if parsed.resource_type != "model_version" or parsed.version is None:
            raise ValidationFailed("model_version_urn must reference a model_version@<n>")
        try:
            return await self.deps.registry.resolve_version(parsed.resource_id, parsed.version)
        except LookupError as exc:
            raise NotFound(f"model version not found: {exc}") from exc
        except (ConnectionError, TimeoutError) as exc:
            raise DependencyUnavailable(f"experiment/MLflow unavailable: {exc}") from exc

    async def _resolve_dataset(self, uow, urn: str, version: int | None) -> ResolvedDataset:
        ds = await uow.inputs.get(urn, version)
        if ds is None:
            raise NotFound(f"input dataset not found: {urn}")
        return ds


class InferenceService(_ResolveMixin):
    def __init__(self, deps: ServiceDeps, *, launch_run=None):
        self.deps = deps
        # launch_run(tenant_id, job_id) schedules real execution (background task
        # in runtime; the integration/unit tests call execute_job directly).
        self._launch_run = launch_run

    # ---- compatibility validation (INF-FR-002/003) ----

    def _build_report(
        self, model: ResolvedModel, ds: ResolvedDataset, *, allow_empty: bool,
        model_handles_missing: bool,
    ) -> CompatibilityReport:
        return validate_compatibility(
            model_inputs=model.inputs,
            dataset_schema=ds.schema,
            model_stage=model.stage,
            row_count=ds.row_count,
            model_handles_missing=model_handles_missing,
            allow_empty=allow_empty,
        )

    def _check_stage(self, ctx: CallCtx, model: ResolvedModel, req: SubmitRequest) -> None:
        allowed = set(self.deps.settings.default_allowed_stages)
        if model.stage in allowed:
            return
        # unpromoted (none/archived) requires the flag AND the permission (BR-2).
        if not req.allow_unpromoted:
            raise ModelStageDenied(
                f"model stage {model.stage!r} not allowed (allowed: {sorted(allowed)})"
            )
        scopes = set(ctx.actor.get("scopes", []) if isinstance(ctx.actor, dict) else [])
        # Agents can never obtain create_unpromoted (toolset exclusion, BR-2).
        if ctx.actor.get("type") == "agent" or "inference.job.create_unpromoted" not in scopes:
            raise PermissionDenied(
                "inference.job.create_unpromoted required for unpromoted models")

    async def validate(self, ctx: CallCtx, req: SubmitRequest) -> dict:
        model = await self._resolve_model(ctx, req.model_version_urn)
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            ds = await self._resolve_dataset(uow, req.input_dataset_urn, None)
        params = req.parameters or {}
        report = self._build_report(
            model, ds, allow_empty=req.allow_empty,
            model_handles_missing=bool(params.get("_model_handles_missing", False)),
        )
        # stage policy folded into the report verdict for the standalone check
        try:
            self._check_stage(ctx, model, req)
        except (ModelStageDenied, PermissionDenied) as exc:
            body = report.as_dict()
            body["compatible"] = False
            body["stage_error"] = exc.code
            return body
        return report.as_dict()

    # ---- submit (INF-FR-001/002/004/008) ----

    async def submit(self, ctx: CallCtx, req: SubmitRequest) -> InferenceJob:
        if self.deps.budget_gate is not None and await self.deps.budget_gate.is_exhausted(
            ctx.tenant_id
        ):
            raise RateLimited("inference_minutes budget exhausted for tenant")
        model = await self._resolve_model(ctx, req.model_version_urn)
        self._check_stage(ctx, model, req)  # 422/403 before any job row (INF-FR-002.1)

        now = self.deps.clock.now()
        job_id = str(uuid7())
        output = req.output or {}
        params = req.parameters or {}
        default_out_name = f"{model.name}-v{model.version}-scores"
        out_name = output.get("dataset_name") or default_out_name
        if not _NAME_RE.match(out_name):
            raise ValidationFailed(
                "output.dataset_name must match ^[a-zA-Z0-9_\\- ]{3,120}$"
            )
        mode = OutputMode[output.get("mode", "create")] if output.get("mode") else OutputMode.create
        job_name = req.name or f"{out_name} @ {now.date().isoformat()}"

        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            if req.schedule_id is None and await uow.jobs.get_by_name(ctx.workspace_id, job_name):
                raise Conflict(f"job name {job_name!r} already exists in workspace")
            ds = await self._resolve_dataset(uow, req.input_dataset_urn, None)
            report = self._build_report(
                model, ds, allow_empty=req.allow_empty,
                model_handles_missing=bool(params.get("_model_handles_missing", False)),
            )
            job = InferenceJob(
                id=job_id, tenant_id=ctx.tenant_id, workspace_id=ctx.workspace_id,
                name=job_name, description=req.description, status=int(JobStatus.validating),
                model_version_urn=req.model_version_urn, model_name=model.name,
                model_version=model.version,
                model_stage_at_submit=int(_stage_enum(model.stage)),
                input_dataset_urn=req.input_dataset_urn, input_dataset_version=ds.version,
                output_mode=int(mode), output_dataset_name=out_name, parameters=params,
                compatibility_report=report.as_dict(), submitted_by=ctx.submitted_by or "",
                via_agent=ctx.via_agent, schedule_id=req.schedule_id,
                created_at=now, updated_at=now,
            )
            await uow.jobs.add(job)
            await self._emit(uow, ctx, "inference.job.created", job, {
                "job_id": job.id, "model_version_urn": job.model_version_urn,
                "input_dataset_urn": job.input_dataset_urn, "schedule_id": job.schedule_id,
                "submitted_by": job.submitted_by, "via_agent": job.via_agent,
            })

            if not report.compatible:
                job.status = int(JobStatus.rejected)
                job.error = {
                    "code": "SCHEMA_INCOMPATIBLE",
                    "message": f"{len(report.violations)} incompatible columns",
                    "details": report.violations,
                }
                job.finished_at = now
                job.updated_at = now
                await uow.jobs.update(job)
                await self._emit(uow, ctx, "inference.job.rejected", job, {
                    "job_id": job.id, "error": {"code": "SCHEMA_INCOMPATIBLE",
                                                "details": report.violations}})
                return job

            # capacity check (INF-FR-008 / BR-12)
            active = await uow.jobs.count_active()
            if active >= self.deps.settings.max_concurrent_inference_jobs:
                depth = await uow.queue.depth()
                if depth >= self.deps.settings.queue_depth_cap:
                    raise RateLimited("inference job queue is full")
                job.status = int(JobStatus.queued)
                job.queued_at = now
                job.updated_at = now
                await uow.jobs.update(job)
                await uow.queue.enqueue(job.id)
                await self._emit(uow, ctx, "inference.job.queued", job,
                                 {"job_id": job.id})
            else:
                await self._mark_submitted(uow, ctx, job, now)

        # launch execution outside the submit transaction for submitted jobs
        if job.status == int(JobStatus.submitted) and self._launch_run is not None:
            await self._launch_run(ctx.tenant_id, job.id)
        return job

    async def _mark_submitted(self, uow, ctx: CallCtx, job: InferenceJob, now) -> None:
        job.status = int(JobStatus.submitted)
        job.pipeline_run_urn = f"wr:{ctx.tenant_id}:pipeline:run/{uuid7()}"
        job.submitted_at = now
        job.updated_at = now
        await uow.jobs.update(job)
        await self._emit(uow, ctx, "inference.job.submitted", job,
                         {"job_id": job.id, "pipeline_run_urn": job.pipeline_run_urn})

    async def bulk(self, ctx: CallCtx, model_version_urn: str, datasets: list[str],
                   base: dict) -> list[dict]:
        if len(datasets) > 20:
            raise ValidationFailed("bulk submit limited to 20 jobs")
        results = []
        for urn in datasets:
            req = SubmitRequest(model_version_urn=model_version_urn, input_dataset_urn=urn,
                                parameters=base.get("parameters"), output=base.get("output"))
            try:
                job = await self.submit(ctx, req)
                results.append({"input_dataset_urn": urn, "job_id": job.id,
                                "status": status_name(job.status)})
            except ValidationFailed as exc:
                results.append({"input_dataset_urn": urn, "error": {"code": exc.code,
                                                                    "message": exc.message}})
        return results

    # ---- execution (real local run substrate) ----

    async def execute_job(self, tenant_id: str, job_id: str) -> None:
        """Run the real scoring for a submitted job and drive it to a terminal
        state. Idempotent: a non-submitted job is a no-op."""
        async with self.deps.uow_factory(tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None or job.status != int(JobStatus.submitted):
                return
        # transition to running
        await self._transition(tenant_id, job_id, JobStatus.running,
                               event="inference.job.started")
        try:
            model = await self.deps.registry.resolve_version(
                parse(job.model_version_urn).resource_id, job.model_version
            )
            async with self.deps.uow_factory(tenant_id) as uow:
                ds = await uow.inputs.get(job.input_dataset_urn, job.input_dataset_version)
            result = await self.deps.executor.run(
                model=model, dataset=ds, job=job, parameters=job.parameters or {}
            )
        except Exception as exc:  # noqa: BLE001 — map to PIPELINE_FAILED
            await self.on_run_failed(tenant_id, job_id, {
                "code": _classify_failure(exc), "component_alias": "inference",
                "message": str(exc)[:500]})
            return
        await self.on_run_succeeded(tenant_id, job_id, result)

    async def on_run_succeeded(self, tenant_id: str, job_id: str, result: ScoringResult) -> None:
        """Finalize in two committed phases (INF-FR-032). Idempotent (AC-12):
        re-entry after a completed finalize is a no-op.

        Phase 1 (txn) — enter ``finalizing`` and register the output dataset
        version. Committed once so a later lineage failure never rolls the output
        back (no re-run; registration is idempotent on job_id).
        Phase 2 — write lineage with bounded retries, then ``succeeded``. If
        lineage never lands, the failure is surfaced as
        ``failed(LINEAGE_REGISTRATION_FAILED)`` (the dataset remains, flagged) —
        never swallowed and never rolled back to ``running`` (BR-4)."""
        # ---- phase 1: register output + move to finalizing (committed) ----
        async with self.deps.uow_factory(tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None or job.status in (int(JobStatus.succeeded), int(JobStatus.failed)):
                return
            if await uow.outputs.version_for_job(job_id) is None:
                # not yet registered: guard the transition then register
                if job.status not in (int(JobStatus.running), int(JobStatus.cancelling),
                                      int(JobStatus.finalizing)):
                    return
                ctx = _job_ctx(job)
                job.status = int(JobStatus.finalizing)
                out_urn, out_version = await self._register_output(uow, ctx, job, result)
                job.output_dataset_urn = out_urn
                job.output_dataset_version = out_version
                job.row_count = result.row_count
                job.components_status = [{"alias": "inference", "phase": "Succeeded"}]
                job.updated_at = self.deps.clock.now()
                await uow.jobs.update(job)
        # ---- phase 2: lineage (bounded retries) + succeed / surfaced failure ----
        await self._finalize_lineage(tenant_id, job_id)
        await self._promote_from_queue(tenant_id)

    async def _finalize_lineage(self, tenant_id: str, job_id: str) -> None:
        attempts = max(1, int(getattr(self.deps.settings, "finalize_max_attempts", 3)))
        last_exc: Exception | None = None
        for _ in range(attempts):
            try:
                async with self.deps.uow_factory(tenant_id) as uow:
                    job = await uow.jobs.get(job_id)
                    if job is None or job.status != int(JobStatus.finalizing):
                        return  # already terminal / not our job
                    ctx = _job_ctx(job)
                    jurn = job_urn(tenant_id, job.id)
                    await self._write_lineage(uow, job, jurn, job.output_dataset_urn,
                                              job.output_dataset_version)
                    job.status = int(JobStatus.succeeded)
                    job.finished_at = self.deps.clock.now()
                    job.updated_at = self.deps.clock.now()
                    await uow.jobs.update(job)
                    await self._emit(uow, ctx, "inference.job.succeeded", job, {
                        "job_id": job.id, "output_dataset_urn": job.output_dataset_urn,
                        "output_dataset_version": job.output_dataset_version,
                        "model_version_urn": job.model_version_urn,
                        "input_dataset_urn": job.input_dataset_urn,
                        "row_count": job.row_count, "duration_s": _duration_s(job)})
                return
            except Exception as exc:  # noqa: BLE001 — retry, then surface
                last_exc = exc
        # retries exhausted: surface as a terminal failure (output remains, flagged)
        async with self.deps.uow_factory(tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None or job.is_terminal:
                return
            ctx = _job_ctx(job)
            job.status = int(JobStatus.failed)
            job.error = {"code": "LINEAGE_REGISTRATION_FAILED", "component_alias": None,
                         "message": f"lineage registration failed: {last_exc}"}
            job.finished_at = self.deps.clock.now()
            job.updated_at = self.deps.clock.now()
            await uow.jobs.update(job)
            await self._emit(uow, ctx, "inference.job.failed", job, {
                "job_id": job.id, "error": job.error, "duration_s": _duration_s(job)})
        if self.deps.notifier is not None:
            await self.deps.notifier.notify(
                tenant_id=tenant_id, recipient=job.submitted_by,
                kind="job_failed", detail={"job_id": job.id, "error": job.error})

    async def on_run_failed(self, tenant_id: str, job_id: str, error: dict) -> None:
        async with self.deps.uow_factory(tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None or job.is_terminal:
                return
            ctx = _job_ctx(job)
            job.status = int(JobStatus.failed)
            job.error = error
            job.finished_at = self.deps.clock.now()
            job.updated_at = self.deps.clock.now()
            await uow.jobs.update(job)
            await self._emit(uow, ctx, "inference.job.failed", job, {
                "job_id": job.id, "error": error, "duration_s": _duration_s(job)})
        if self.deps.notifier is not None:
            await self.deps.notifier.notify(
                tenant_id=tenant_id, recipient=job.submitted_by, kind="job_failed",
                detail={"job_id": job.id, "error": error})
        await self._promote_from_queue(tenant_id)

    async def _register_output(self, uow, ctx, job, result: ScoringResult) -> tuple[str, int]:
        mode = OutputMode(job.output_mode)
        existing = await uow.outputs.find(job.workspace_id, job.output_dataset_name)
        # ownership is model-level so re-runs / promotions append to the same
        # output dataset (INF-FR-031: owned by a prior job of the same model).
        model_urn = f"wr:{ctx.tenant_id}:experiment:model/{job.model_name}"
        if mode == OutputMode.create:
            if existing is not None:
                raise Conflict(f"output dataset {job.output_dataset_name!r} already exists")
            ds_row = await uow.outputs.create_dataset(
                workspace_id=job.workspace_id, name=job.output_dataset_name,
                urn=f"wr:{ctx.tenant_id}:dataset:dataset/{uuid7()}", owner_model_urn=model_urn)
            version_no = 1
        elif existing is None:
            # append/replace first fire: create the target, owned by this model (BR-11)
            ds_row = await uow.outputs.create_dataset(
                workspace_id=job.workspace_id, name=job.output_dataset_name,
                urn=f"wr:{ctx.tenant_id}:dataset:dataset/{uuid7()}", owner_model_urn=model_urn)
            version_no = 1
        else:  # append / replace target must be owned by the same model
            if existing.owner_model_urn != model_urn:
                from app.domain.errors import OutputNotOwned

                raise OutputNotOwned("append/replace target not owned by this model")
            ds_row = existing
            version_no = existing.current_version + 1
        await uow.outputs.add_version(
            dataset_id=ds_row.id, version_no=version_no, storage_uri=result.output_storage_uri,
            snapshot_id=result.snapshot_id, row_count=result.row_count, job_id=job.id)
        await uow.outputs.bump_version(ds_row, version_no)
        return ds_row.urn, version_no

    async def _write_lineage(self, uow, job, jurn, out_urn, out_version) -> None:
        now = self.deps.clock.now()
        edges = [
            (job.model_version_urn, jurn, "used_by"),
            (job.input_dataset_urn, jurn, "input_to"),
            (jurn, f"{out_urn}@{out_version}", "produced"),
        ]
        for src, dst, activity in edges:
            await uow.lineage.upsert(LineageEdge(
                id=str(uuid7()), tenant_id=job.tenant_id, from_urn=src, to_urn=dst,
                activity=activity, run_urn=jurn, properties=None,
                occurred_at=now, created_at=now))

    # ---- cancel / retry / delete (INF-FR-006/007) ----

    async def cancel(self, ctx: CallCtx, job_id: str) -> InferenceJob:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None:
                raise NotFound("inference job not found")
            if JobStatus(job.status) in (JobStatus.cancelled, JobStatus.cancelling):
                return job  # idempotent (AC-11)
            if JobStatus(job.status) not in CANCELLABLE:
                raise Conflict(f"job in {status_name(job.status)} is not cancellable")
            now = self.deps.clock.now()
            if JobStatus(job.status) in (JobStatus.queued,):
                await uow.queue.remove(job.id)
                job.status = int(JobStatus.cancelled)
                job.finished_at = now
            else:
                # submitted/running → cancelling (terminate forwarded to executor)
                job.status = int(JobStatus.cancelling)
            job.updated_at = now
            await uow.jobs.update(job)
            await self._emit(uow, ctx, "inference.job.cancelled", job,
                             {"job_id": job.id, "cancelled_by": ctx.actor.get("id")})
        if JobStatus(job.status) == JobStatus.cancelled:
            await self._promote_from_queue(ctx.tenant_id)
        else:
            # confirm the cancel (local executor terminate is cooperative)
            await self._confirm_cancel(ctx.tenant_id, job.id)
        return job

    async def _confirm_cancel(self, tenant_id: str, job_id: str) -> None:
        async with self.deps.uow_factory(tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None or job.status != int(JobStatus.cancelling):
                return
            ctx = _job_ctx(job)
            job.status = int(JobStatus.cancelled)
            job.finished_at = self.deps.clock.now()
            job.updated_at = self.deps.clock.now()
            await uow.jobs.update(job)
            await self._emit(uow, ctx, "inference.job.cancelled", job,
                             {"job_id": job.id, "cancelled_by": ctx.actor.get("id")})
        await self._promote_from_queue(tenant_id)

    async def retry(self, ctx: CallCtx, job_id: str) -> InferenceJob:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None:
                raise NotFound("inference job not found")
            if JobStatus(job.status) not in TERMINAL_FAILURE:
                raise Conflict("retry allowed only from a terminal failure state")
        req = SubmitRequest(
            model_version_urn=job.model_version_urn, input_dataset_urn=job.input_dataset_urn,
            parameters=job.parameters, output={"dataset_name": job.output_dataset_name,
                                               "mode": OutputMode(job.output_mode).name})
        new = await self.submit(ctx, req)
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            fresh = await uow.jobs.get(new.id)
            if fresh is not None:
                fresh.retried_from_job_id = job_id
                await uow.jobs.update(fresh)
                new = fresh
        return new

    async def delete(self, ctx: CallCtx, job_id: str) -> None:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None:
                raise NotFound("inference job not found")
            if not job.is_terminal:
                raise Conflict("only terminal jobs can be deleted")
            job.deleted_at = self.deps.clock.now()
            job.updated_at = self.deps.clock.now()
            await uow.jobs.update(job)

    async def get(self, ctx: CallCtx, job_id: str) -> InferenceJob:
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None:
                raise NotFound("inference job not found")
            return job

    async def list(self, ctx: CallCtx, filters: Filters, sort: str, limit: int,
                   cursor: str | None):
        async with self.deps.uow_factory(ctx.tenant_id) as uow:
            return await uow.jobs.list(filters, sort, limit, cursor)

    # ---- shared transition + queue helpers ----

    async def _transition(self, tenant_id: str, job_id: str, dst: JobStatus,
                          *, event: str | None = None) -> None:
        async with self.deps.uow_factory(tenant_id) as uow:
            job = await uow.jobs.get(job_id)
            if job is None or not can_transition(job.status, int(dst)):
                return
            prev = job.status
            job.status = int(dst)
            if dst == JobStatus.running:
                job.started_at = self.deps.clock.now()
            job.updated_at = self.deps.clock.now()
            await uow.jobs.update(job)
            ctx = _job_ctx(job)
            await self._emit(uow, ctx, "inference.job.status_changed", job,
                             {"job_id": job.id, "status": status_name(job.status),
                              "previous_status": status_name(prev)})
            if event:
                await self._emit(uow, ctx, event, job,
                                 {"job_id": job.id, "pipeline_run_urn": job.pipeline_run_urn})

    async def _promote_from_queue(self, tenant_id: str) -> None:
        launch_id = None
        async with self.deps.uow_factory(tenant_id) as uow:
            active = await uow.jobs.count_active()
            if active >= self.deps.settings.max_concurrent_inference_jobs:
                return
            job_id = await uow.queue.next_job_id()
            if job_id is None:
                return
            job = await uow.jobs.get(job_id)
            if job is None or job.status != int(JobStatus.queued):
                await uow.queue.remove(job_id)
                return
            await uow.queue.remove(job_id)
            ctx = _job_ctx(job)
            await self._mark_submitted(uow, ctx, job, self.deps.clock.now())
            launch_id = job.id
        if launch_id and self._launch_run is not None:
            await self._launch_run(tenant_id, launch_id)

    async def _emit(self, uow, ctx: CallCtx, event_type: str, job: InferenceJob,
                    payload: dict) -> None:
        env = make_envelope(event_type=event_type, ctx=ctx,
                            resource_urn=job_urn(job.tenant_id, job.id), payload=payload)
        await uow.outbox.add(EVENTS_TOPIC, env)

    # ---- reaper (INF-FR-042, BR-12) ----

    async def reap(self, tenant_id: str) -> int:
        """Fail jobs stuck past their timeout (INF-FR-042, BR-12):
        - running jobs (submitted/running/finalizing/cancelling) past
          ``max_run_duration`` (default 8h);
        - queued jobs past ``queued_timeout`` (default 60 min) — a separate,
          shorter window (the two must not share the 8h cutoff)."""
        reaped = 0
        settings = self.deps.settings
        from datetime import timedelta

        now = self.deps.clock.now()
        run_cutoff = now - timedelta(hours=settings.max_run_duration_hours)
        queue_cutoff = now - timedelta(minutes=settings.queued_timeout_minutes)
        async with self.deps.uow_factory(tenant_id, worker=True) as uow:
            run_stuck = await uow.jobs.running_started_before(run_cutoff)
            queue_stuck = await uow.jobs.queued_before(queue_cutoff)
        for job in run_stuck:
            await self.on_run_failed(job.tenant_id, job.id, {
                "code": "QUOTA_TIMEOUT", "component_alias": None,
                "message": "reaped: exceeded max run duration"})
            reaped += 1
        for job in queue_stuck:
            # drop from the queue then fail so a freed slot promotes the next job
            async with self.deps.uow_factory(job.tenant_id) as uow:
                await uow.queue.remove(job.id)
            await self.on_run_failed(job.tenant_id, job.id, {
                "code": "QUOTA_TIMEOUT", "component_alias": None,
                "message": "reaped: exceeded queued timeout"})
            reaped += 1
        return reaped


def _stage_enum(stage: str) -> ModelStage:
    from app.domain.enums import stage_from_mlflow

    return stage_from_mlflow(stage)


def _job_ctx(job: InferenceJob) -> CallCtx:
    return CallCtx(
        tenant_id=job.tenant_id, actor={"type": "service", "id": "inference-service"},
        via_agent=None, workspace_id=job.workspace_id, submitted_by=job.submitted_by)


def _duration_s(job: InferenceJob) -> float | None:
    if job.started_at and job.finished_at:
        return round((job.finished_at - job.started_at).total_seconds(), 3)
    return None


def _classify_failure(exc: Exception) -> str:
    msg = str(exc).lower()
    if isinstance(exc, MemoryError) or "out of memory" in msg:
        return "OUT_OF_MEMORY"
    if isinstance(exc, TimeoutError) or "timeout" in msg:
        return "COMPONENT_TIMEOUT"
    if isinstance(exc, (ConnectionError,)) or "connection" in msg:
        return "DEPENDENCY_UNAVAILABLE"
    return "PIPELINE_FAILED"
