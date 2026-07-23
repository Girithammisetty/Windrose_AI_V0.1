"""Application services: catalog, templates/versions/validation/compilation, and the
run lifecycle (submit → real training via the local executor → lifecycle events).

Events are written to the transactional outbox inside the same UoW as the state
change (MASTER-FR-034); the OutboxDispatcher relays them to Kafka. Nothing is
published before it commits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.domain.algorithms import instantiate
from app.domain.compiler import compile_workflow_template
from app.domain.dag import validate_definition
from app.domain.entities import (
    CallCtx,
    PipelineRun,
    PipelineTemplate,
    TemplateVersion,
    TenantQuota,
)
from app.domain.enums import (
    NON_RUNNABLE_TYPES,
    ModelType,
    PipelineType,
    RunStatus,
    model_type_from_str,
    pipeline_type_from_str,
)
from app.domain.errors import (
    CannotCompile,
    CannotRunPipelineType,
    Conflict,
    NotFound,
    RateLimited,
    ValidationFailed,
)
from app.domain.ports import RunFilters, TemplateFilters, TrainingSpec
from app.domain.resources import PLATFORM_CEILING
from app.events.envelope import make_envelope, run_urn, template_urn
from app.utils import new_id


@dataclass
class ServiceDeps:
    settings: Any
    clock: Any
    uow_factory: Any
    components: dict
    algorithms: dict
    manifest_store: Any
    executor: Any
    mlflow: Any
    feature_source: Any = None
    dataset_reader: Any = None  # HttpDatasetReader (real) / InMemoryDatasetReader (unit)
    workflow_backend: Any = None  # ArgoWorkflowExecutor when executor_backend="argo"
    events_topic: str = "pipeline.events.v1"
    extras: dict = field(default_factory=dict)


class CatalogService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps

    def list_components(self):
        # Only user-authorable components appear in the catalog view. Internal
        # utility nodes (clone-input, data-profiler; internal_component_type > 0)
        # are injected by the compiler, never composed by hand — exclude them so
        # they can't be dropped into a builder canvas.
        return sorted(
            (c for c in self.d.components.values() if not getattr(c, "internal_component_type", 0)),
            key=lambda c: (c.component_type, c.name),
        )

    def get_component(self, name: str):
        comp = self.d.components.get(name)
        if comp is None:
            raise NotFound(f"component {name!r} not found")
        return comp

    def list_algorithms(self):
        return sorted(self.d.algorithms.values(), key=lambda a: a.order)

    def get_algorithm(self, name: str):
        algo = self.d.algorithms.get(name)
        if algo is None:
            raise NotFound(f"algorithm template {name!r} not found")
        return algo


def _ceiling(quota: TenantQuota | None) -> dict:
    if quota and quota.resource_ceiling:
        return {**PLATFORM_CEILING, **quota.resource_ceiling}
    return dict(PLATFORM_CEILING)


class TemplateService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps

    def _components_for(self, ctx: CallCtx) -> dict:
        # Tenant catalog view (per-tenant enablement would filter here; BR-11).
        return self.d.components

    async def validate(self, ctx: CallCtx, definition: dict, *, pipeline_type: str,
                       model_type: str | None, mode: str = "all"):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            quota = await uow.quotas.get(ctx.tenant_id)
        report = validate_definition(
            definition, pipeline_type=pipeline_type_from_str(pipeline_type),
            model_type=model_type, components=self._components_for(ctx),
            quota_ceiling=_ceiling(quota), mode=mode)
        return report

    async def create(self, ctx: CallCtx, body: dict) -> tuple[PipelineTemplate,
                                                              TemplateVersion]:
        pt = pipeline_type_from_str(body["pipeline_type"])
        mt = model_type_from_str(body.get("model_type"))
        definition = body.get("definition") or {"nodes": [], "edges": []}
        now = self.d.clock.now()
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            if await uow.templates.get_by_name(body["workspace_id"], body["name"]):
                raise Conflict(f"template name {body['name']!r} already exists in workspace")
            quota = await uow.quotas.get(ctx.tenant_id)
            report = validate_definition(
                definition, pipeline_type=pt,
                model_type=body.get("model_type"), components=self._components_for(ctx),
                quota_ceiling=_ceiling(quota), mode="all")
            template = PipelineTemplate(
                id=new_id(), tenant_id=ctx.tenant_id, workspace_id=body["workspace_id"],
                name=body["name"], pipeline_type=int(pt),
                model_type=int(mt) if mt is not None else None,
                algorithm_template_name=body.get("algorithm_template_name"),
                active_version_id=None, is_system=bool(body.get("is_system")),
                created_by=ctx.actor.get("id"), created_at=now, updated_at=now)
            version = self._new_version(ctx, template, definition, report, 1,
                                        body.get("run_parameters") or {})
            template.active_version_id = version.id
            await uow.templates.add(template)
            await uow.versions.add(version)
            await self._emit(uow, ctx, "pipeline.template.created", template)
        return template, version

    def _new_version(self, ctx, template, definition, report, version_no,
                     run_parameters) -> TemplateVersion:
        gp = ((definition.get("metadata") or {}).get("global_parameters")) or []
        return TemplateVersion(
            id=new_id(), tenant_id=ctx.tenant_id, template_id=template.id,
            version_no=version_no, definition=definition,
            validation_status=1 if report.valid else 0,
            validation_report=report.to_dict(), run_parameters=run_parameters,
            global_parameters=list(gp),
            component_catalog_version=self.d.settings.component_catalog_version,
            compiled_manifest_ref=None, manifest_digest=None, argo_template_name=None,
            created_by=ctx.actor.get("id"), created_at=self.d.clock.now())

    async def update(self, ctx: CallCtx, template_id: str, body: dict,
                     *, if_match: str | None = None):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id)
            if template is None:
                raise NotFound("template not found")
            if if_match is not None and if_match != template.active_version_id:
                raise Conflict("template changed since last read (If-Match)")
            definition = body.get("definition")
            if definition is None:
                latest = await uow.versions.get_by_id(template.active_version_id)
                definition = latest.definition if latest else {"nodes": [], "edges": []}
            if "name" in body and body["name"] != template.name:
                if await uow.templates.get_by_name(template.workspace_id, body["name"]):
                    raise Conflict("template name already exists")
                template.name = body["name"]
            quota = await uow.quotas.get(ctx.tenant_id)
            report = validate_definition(
                definition, pipeline_type=PipelineType(template.pipeline_type),
                model_type=(ModelType(template.model_type).name
                            if template.model_type is not None else None),
                components=self._components_for(ctx),
                quota_ceiling=_ceiling(quota), mode="all")
            no = await uow.versions.next_version_no(template_id)
            version = self._new_version(ctx, template, definition, report, no,
                                        body.get("run_parameters")
                                        or template_run_params(template))
            template.active_version_id = version.id
            template.updated_at = self.d.clock.now()
            await uow.versions.add(version)
            await uow.templates.update(template)
            await self._emit(uow, ctx, "pipeline.template.updated", template,
                             version_id=version.id)
        return template, version

    async def get(self, ctx, template_id):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id)
            if template is None:
                raise NotFound("template not found")
            version = await uow.versions.get_by_id(template.active_version_id)
        return template, version

    async def list(self, ctx, filters: TemplateFilters, limit, cursor):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            return await uow.templates.list(filters, limit, cursor)

    async def versions(self, ctx, template_id, limit, cursor):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            if await uow.templates.get(template_id) is None:
                raise NotFound("template not found")
            return await uow.versions.list(template_id, limit, cursor)

    async def activate_version(self, ctx, template_id, version_id):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id)
            if template is None:
                raise NotFound("template not found")
            version = await uow.versions.get_by_id(version_id)
            if version is None or version.template_id != template_id:
                raise NotFound("version not found")
            template.active_version_id = version_id
            template.updated_at = self.d.clock.now()
            await uow.templates.update(template)
            await self._emit(uow, ctx, "pipeline.template.version_activated", template,
                             version_id=version_id)
        return template, version

    async def archive(self, ctx, template_id):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id)
            if template is None:
                raise NotFound("template not found")
            if template.is_system:
                raise Conflict("system-owned templates cannot be archived")
            template.deleted_at = self.d.clock.now()
            template.updated_at = template.deleted_at
            await uow.templates.update(template)
            await self._emit(uow, ctx, "pipeline.template.archived", template)
        return template

    async def restore(self, ctx, template_id):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id, include_deleted=True)
            if template is None:
                raise NotFound("template not found")
            template.deleted_at = None
            template.updated_at = self.d.clock.now()
            await uow.templates.update(template)
            await self._emit(uow, ctx, "pipeline.template.restored", template)
        return template

    async def clone(self, ctx, template_id):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            src = await uow.templates.get(template_id)
            if src is None:
                raise NotFound("template not found")
            version = await uow.versions.get_by_id(src.active_version_id)
            base = f"Copy of {src.name}"
            name = base
            i = 2
            while await uow.templates.get_by_name(src.workspace_id, name):
                name = f"{base} ({i})"
                i += 1
            now = self.d.clock.now()
            clone = PipelineTemplate(
                id=new_id(), tenant_id=ctx.tenant_id, workspace_id=src.workspace_id,
                name=name, pipeline_type=src.pipeline_type, model_type=src.model_type,
                algorithm_template_name=src.algorithm_template_name,
                active_version_id=None, is_system=False, created_by=ctx.actor.get("id"),
                created_at=now, updated_at=now)
            report = _ReportShim(version.validation_status == 1,
                                 version.validation_report)
            nv = self._new_version(ctx, clone, version.definition, report, 1,
                                   version.run_parameters)
            clone.active_version_id = nv.id
            await uow.templates.add(clone)
            await uow.versions.add(nv)
            await self._emit(uow, ctx, "pipeline.template.created", clone)
        return clone, nv

    async def compile(self, ctx, template_id):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id)
            if template is None:
                raise NotFound("template not found")
            version = await uow.versions.get_by_id(template.active_version_id)
            manifest = await self._ensure_compiled(uow, ctx, template, version)
        return template, version, manifest

    async def _ensure_compiled(self, uow, ctx, template, version) -> dict:
        if version.validation_status != 1:
            raise CannotCompile("cannot compile a draft (invalid) version",
                                details=(version.validation_report or {}).get("items"))
        quota = await uow.quotas.get(ctx.tenant_id)
        argo_name = version.argo_template_name or (
            f"wf-{template.id[:8]}-v{version.version_no}")
        manifest, digest = compile_workflow_template(
            version.definition, tenant_id=ctx.tenant_id, template_id=template.id,
            version_id=version.id, pipeline_type=PipelineType(template.pipeline_type),
            components=self.d.components, argo_template_name=argo_name,
            quota_ceiling=_ceiling(quota))
        if version.manifest_digest == digest and version.compiled_manifest_ref:
            stored = await self.d.manifest_store.get(version.compiled_manifest_ref)
            return stored or manifest
        ref = await self.d.manifest_store.put(
            f"manifests/{ctx.tenant_id}/{version.id}.json", manifest)
        version.compiled_manifest_ref = ref
        version.manifest_digest = digest
        version.argo_template_name = argo_name
        await uow.versions.update(version)
        await self._emit(uow, ctx, "pipeline.template.compiled", template,
                         version_id=version.id, extra={"manifest_digest": digest,
                                                       "argo_template_name": argo_name})
        return manifest

    async def _emit(self, uow, ctx, event_type, template, *, version_id=None, extra=None):
        payload = {"template_id": template.id, "version_id":
                   version_id or template.active_version_id,
                   "pipeline_type": PipelineType(template.pipeline_type).name,
                   "name": template.name, "workspace_id": template.workspace_id}
        if extra:
            payload.update(extra)
        env = make_envelope(
            event_type=event_type, tenant_id=ctx.tenant_id, actor=ctx.actor,
            via_agent=ctx.via_agent,
            resource_urn=template_urn(ctx.tenant_id, template.id), payload=payload,
            trace_id=ctx.trace_id)
        await uow.outbox.add(self.d.events_topic, env)


def template_run_params(template) -> dict:
    return {}


@dataclass
class _ReportShim:
    valid: bool
    _report: dict

    def to_dict(self):
        return self._report or {"status": "valid" if self.valid else "draft",
                                "items": []}


class RunService:
    def __init__(self, deps: ServiceDeps, template_service: TemplateService):
        self.d = deps
        self.templates = template_service

    async def create_run(self, ctx: CallCtx, template_id: str, run_parameters: dict,
                         *, retried_from: str | None = None, trigger: str = "manual"):
        pt_runnable_error = None
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id)
            if template is None:
                raise NotFound("template not found")
            if PipelineType(template.pipeline_type) in NON_RUNNABLE_TYPES:
                pt_runnable_error = PipelineType(template.pipeline_type).name
        if pt_runnable_error:
            raise CannotRunPipelineType(
                f"{pt_runnable_error} pipelines are not directly runnable")

        # Rate limit (PIPE-FR-038, AC-9) — outside the write txn for a clean read.
        await self._rate_limit(ctx)

        async with self.d.uow_factory(ctx.tenant_id) as uow:
            template = await uow.templates.get(template_id)
            version = await uow.versions.get_by_id(template.active_version_id)
            if version is None or version.validation_status != 1:
                raise ValidationFailed("active version is a draft; fix validation first",
                                       code="VALIDATION_FAILED")
            merged = {**version.run_parameters, **(run_parameters or {})}
            quota = await uow.quotas.get(ctx.tenant_id)
            report = validate_definition(
                version.definition, pipeline_type=PipelineType(template.pipeline_type),
                model_type=(ModelType(template.model_type).name
                            if template.model_type is not None else None),
                components=self.d.components, quota_ceiling=_ceiling(quota), mode="all")
            if not report.valid:
                raise ValidationFailed("run parameter validation failed",
                                       code="VALIDATION_FAILED", details=report.items)
            await self.templates._ensure_compiled(uow, ctx, template, version)

            # BR-15: create the MLflow run BEFORE persisting/submitting. A retrain
            # can target the experiment-service experiment (by MLflow experiment id or
            # name) so the mirror reconciliation sweep materializes the run into
            # experiment-service; otherwise it lands in the shared orchestrator experiment.
            # workspace_id rides along as a run tag (datacern.workspace_id after the
            # gateway prefixes it) so experiment-service's registry mirror can place
            # the mirrored model in the tenant workspace the run belongs to — the
            # MLflow experiment name is unreliable for that (agent runs land in the
            # shared orchestrator experiment, whose name encodes a different workspace).
            mlflow_run_id = await self.d.mlflow.create_run(
                tags={"tenant_id": ctx.tenant_id, "template_id": template.id,
                      "workspace_id": template.workspace_id},
                experiment_id=merged.get("mlflow_experiment_id"),
                experiment_name=(merged.get("mlflow_experiment")
                                 or f"{ctx.tenant_id}/{template.workspace_id}/pipelines"))

            quota = quota or TenantQuota(tenant_id=ctx.tenant_id)
            active = await uow.runs.count_active(ctx.tenant_id)
            now = self.d.clock.now()
            run_id = new_id()
            # Derive the argo_workflow_name suffix from the run's OWN id random tail,
            # NOT a second new_id()[:8]: new_id() is a UUID7, whose first 8 hex chars
            # are the millisecond-timestamp prefix — identical for any two runs of the
            # same template submitted in the same coarse time window, which collides on
            # the argo_workflow_name UNIQUE constraint and surfaces as an opaque 500
            # (confirmed live 2026-07-17 on back-to-back retrains of one template). The
            # last 12 hex of a UUID7 are 62 random bits, so this is collision-safe and
            # ties the workflow name to the real run id.
            run = PipelineRun(
                id=run_id, tenant_id=ctx.tenant_id, template_id=template.id,
                version_id=version.id, status=int(RunStatus.pending),
                argo_workflow_name=f"{version.argo_template_name}-{run_id.replace('-', '')[-12:]}",
                mlflow_run_id=mlflow_run_id, run_parameters=merged,
                components_status={}, error=None,
                input_dataset_urns=self._input_urns(version.definition, merged),
                output_dataset_urns=[], retried_from_run_id=retried_from,
                submitted_by=ctx.actor.get("id"), via_agent=ctx.via_agent,
                model_uri=None, metrics=None, created_at=now, updated_at=now)

            if active >= quota.max_concurrent_runs:
                depth = await uow.run_queue.depth(ctx.tenant_id)
                if depth >= self.d.settings.max_queue_depth:
                    from app.domain.errors import BudgetExhausted
                    raise BudgetExhausted("run queue is full")
                run.status = int(RunStatus.quota_queued)
                run.queued_at = now
                await uow.runs.add(run)
                await uow.run_queue.enqueue(run.id, ctx.tenant_id, now)
                await self._emit_run(uow, ctx, "pipeline.run.quota_queued", run,
                                     {"queue_position": depth + 1, "trigger": trigger})
            else:
                run.status = int(RunStatus.submitted)
                run.submitted_at = now
                await uow.runs.add(run)
                await self._emit_run(uow, ctx, "pipeline.run.submitted", run, {
                    "template_id": template.id, "version_id": version.id,
                    "mlflow_run_id": mlflow_run_id,
                    "argo_workflow_name": run.argo_workflow_name,
                    "submitted_by": run.submitted_by, "via_agent": ctx.via_agent,
                    "trigger": trigger})
        return f"op_{new_id()}", run

    async def _rate_limit(self, ctx: CallCtx):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            quota = await uow.quotas.get(ctx.tenant_id)
            last = await uow.runs.last_submission_at(ctx.tenant_id, ctx.actor.get("id"))
        min_gap = (quota.min_seconds_between_runs if quota
                   else self.d.settings.default_min_seconds_between_runs)
        if last is not None:
            elapsed = (self.d.clock.now() - last).total_seconds()
            if elapsed < min_gap:
                retry_after = int(math.ceil(min_gap - elapsed))
                raise RateLimited(f"submit again in {retry_after}s",
                                  retry_after=retry_after)

    def _input_urns(self, definition, params) -> list[str]:
        urns = []
        for node in definition.get("nodes", []):
            if node.get("component") in ("read-from-warehouse",
                                         "batch-read-from-warehouse"):
                ds = (node.get("parameters") or {}).get("dataset")
                if ds:
                    urns.append(ds)
        if params.get("labeled_dataset_urn"):
            urns.append(params["labeled_dataset_urn"])
        return urns

    # ---- execution (local executor path; Argo path via the informer adapter) ----

    async def drive_run(self, tenant_id: str, run_id: str):
        """Advance a submitted run to a terminal state by running REAL training via
        the executor, updating status + emitting lifecycle events at each step."""
        ctx = CallCtx(tenant_id=tenant_id, actor={"type": "service",
                                                  "id": "pipeline-orchestrator"})
        async with self.d.uow_factory(tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if run is None or run.status != int(RunStatus.submitted):
                return run
            template = await uow.templates.get(run.template_id)
            version = await uow.versions.get_by_id(run.version_id)
            prev = run.status
            run.status = int(RunStatus.running)
            run.started_at = self.d.clock.now()
            run.components_status = {"train-1": {"alias": "train-1", "phase": "Running",
                                                 "started_at": run.started_at.isoformat()}}
            await uow.runs.update(run)
            await self._emit_run(uow, ctx, "pipeline.run.started", run,
                                 {"argo_workflow_name": run.argo_workflow_name,
                                  "started_at": run.started_at.isoformat()})
            await self._emit_status_changed(uow, ctx, run, prev)

        # BRD 62 inc3: data-prep / feature-engineering / profiling / scheduled runs
        # execute the operator DAG locally (real pandas) and persist the output via the
        # warehouse sink (BRD 65) — the classic-pipeline path, distinct from training.
        ptype = template.pipeline_type if template else None
        is_dataprep = ptype in (int(PipelineType.data_prep), int(PipelineType.profiling),
                                int(PipelineType.scheduled))
        try:
            if (self.d.settings.executor_backend == "argo"
                    and self.d.workflow_backend is not None):
                # INFRA-GATED real path: compile + submit to the Argo server (raises
                # DependencyUnavailable when no k8s cluster/Argo server is reachable).
                await self._drive_argo(tenant_id, run, template, version)
            elif is_dataprep:
                await self._drive_data_prep(ctx, run, template, version)
            else:
                spec = await self._build_training_spec(tenant_id, run, template, version)
                result = await self.d.executor.execute_training(spec)
                await self._finish_success(ctx, run_id, result)
        except Exception as exc:  # noqa: BLE001 — surface as a failed run
            await self._finish_failure(ctx, run_id, exc)
        await self._dequeue_next(ctx)
        async with self.d.uow_factory(tenant_id) as uow:
            return await uow.runs.get(run_id)

    async def _drive_argo(self, tenant_id, run, template, version) -> None:
        """INFRA-GATED: submit the compiled workflow to the real Argo server. Without a
        reachable k8s cluster this raises DependencyUnavailable, which surfaces as a
        failed run (the documented infra-gated path)."""
        manifest = None
        if version.compiled_manifest_ref:
            manifest = await self.d.manifest_store.get(version.compiled_manifest_ref)
        if manifest is None:
            raise CannotCompile("no compiled manifest for argo submission")
        await self.d.workflow_backend.submit(
            tenant_id, manifest,
            {"mlflow_run_id": run.mlflow_run_id, "current_context": tenant_id})
        # A real informer would now drive status → Kafka; unreachable infra never gets
        # here (submit already raised DependencyUnavailable on the Mac).

    async def _drive_data_prep(self, ctx, run, template, version) -> None:
        """BRD 62 inc3: run a data-prep DAG locally over real dataset rows and persist
        the computed output(s) via the BRD 65 warehouse sink. Real end to end (pandas
        + MinIO/S3) with no Argo; a node/sink failure surfaces as a failed run."""
        import pandas as pd

        from app.executor.local_pipeline import LocalPipelineExecutor
        from app.executor.sinks import WAREHOUSE_SINKS

        definition = version.definition or {}
        tenant_id = run.tenant_id
        # Pre-read every input dataset's rows (async) into frames the pure executor reads.
        frames: dict[str, pd.DataFrame] = {}
        for node in definition.get("nodes", []):
            if node.get("component") in ("read-from-warehouse", "batch-read-from-warehouse"):
                ds = (node.get("parameters") or {}).get("dataset")
                if ds and ds not in frames:
                    if self.d.dataset_reader is None:
                        raise CannotCompile("no dataset reader configured for a data-prep run")
                    rows = await self.d.dataset_reader.read_rows(tenant_id, ds)
                    frames[ds] = pd.DataFrame([dict(r) for r in rows])

        sink = WAREHOUSE_SINKS.create(self.d.settings.warehouse_sink, self.d.settings)
        base_name = (template.name if template else "pipeline").replace(" ", "_")
        written: dict[str, Any] = {}

        def _reader(urn, _params):
            if urn not in frames:
                raise CannotCompile(f"input dataset {urn!r} not available")
            return frames[urn].copy()

        def _writer(frame, alias, params):
            name = ((params or {}).get("output_dataset_name")
                    or (params or {}).get("dataset_name") or f"{base_name}_{alias}")
            res = sink.write_frame(frame, tenant_id=tenant_id, name=name)
            written[alias] = res
            return res.ref

        # Pure pandas over an in-memory frame — fast + non-blocking, so run inline
        # (no thread hand-off; unlike the heavy sklearn/MLflow training path).
        result = LocalPipelineExecutor(reader=_reader, writer=_writer).run(definition)
        await self._finish_data_prep_success(ctx, run.id, result, written)

    async def _finish_data_prep_success(self, ctx, run_id, result, written) -> None:
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            prev = run.status
            run.status = int(RunStatus.succeeded)
            run.finished_at = self.d.clock.now()
            run.output_dataset_urns = [w.ref for w in written.values()]
            total_rows = sum(w.rows for w in written.values())
            run.metrics = {"output_rows": float(total_rows),
                           "outputs": float(len(written)),
                           "nodes": float(len(result.statuses))}
            run.components_status = {
                s.alias: {"alias": s.alias, "component": s.component, "phase": s.phase,
                          "rows_out": s.rows_out,
                          "finished_at": run.finished_at.isoformat()}
                for s in result.statuses}
            await uow.runs.update(run)
            duration = (run.finished_at - (run.started_at or run.finished_at)).total_seconds()
            await self._emit_run(uow, ctx, "pipeline.run.succeeded", run, {
                "duration_s": duration, "output_dataset_urns": run.output_dataset_urns,
                "metrics": run.metrics})
            await self._emit_status_changed(uow, ctx, run, prev)

    async def _build_training_spec(self, tenant_id, run, template, version) -> TrainingSpec:
        params = dict(run.run_parameters or {})
        algorithm = (params.get("algorithm")
                     or (template.algorithm_template_name if template else None)
                     or (version.definition.get("metadata", {}).get("algorithm"))
                     or self._algo_from_definition(version.definition)
                     or "xgboost")
        model_type = (ModelType(template.model_type).name
                      if template and template.model_type is not None else "classification")
        label_column = params.get("label_column") or "label"
        rows, feature_cols = await self._assemble_rows(tenant_id, run, version,
                                                       label_column)
        hyper = {k: v for k, v in params.items()
                 if k not in {"algorithm", "label_column", "labeled_dataset_urn",
                              "dataset_urn", "training_data",
                              "mlflow_experiment", "mlflow_experiment_id"}}
        reg_name = f"{template.name if template else 'model'}".replace(" ", "_")
        reg_name = f"wr_{tenant_id[:8]}_{reg_name}"[:120]
        return TrainingSpec(
            tenant_id=tenant_id, run_id=run.id, algorithm=algorithm,
            model_type=model_type, params=hyper, rows=rows,
            feature_columns=feature_cols, label_column=label_column,
            # The run already exists in MLflow (resumed by mlflow_run_id), so this
            # experiment name only matters for the (unused) fresh-run path; prefer the
            # retrain target name when supplied.
            experiment=(params.get("mlflow_experiment")
                        or self.d.settings.mlflow_experiment),
            registered_model_name=reg_name, mlflow_run_id=run.mlflow_run_id,
            tags={"run_id": run.id, "template_id": run.template_id})

    def _algo_from_definition(self, definition) -> str | None:
        for node in definition.get("nodes", []):
            comp = node.get("component", "")
            if comp == "hyperparameter-search":
                return (node.get("parameters") or {}).get("algorithm")
            if comp.endswith("-train"):
                return comp[:-len("-train")]
        return None

    async def _assemble_rows(self, tenant_id, run, version, label_column):
        """Assemble the labeled training frame. The learning-loop retrain path reads
        the assembled labeled_examples for a dataset_urn (corrections → rows); an
        inline ``training_data`` param or an object-store feature CSV are also
        supported."""
        params = run.run_parameters or {}
        if params.get("training_data"):
            rows = list(params["training_data"])
            cols = [c for c in (rows[0].keys() if rows else []) if c != label_column]
            return rows, cols

        ds_urn = (params.get("labeled_dataset_urn")
                  or self._first_read_dataset(version.definition))
        rows: list[dict] = []
        if ds_urn:
            async with self.d.uow_factory(tenant_id) as uow:
                examples = await uow.labeled_examples.list_for_dataset(ds_urn)
            for ex in examples:
                rows.append({**ex.features, label_column: ex.label})
        if not rows and ds_urn and self.d.dataset_reader is not None:
            # No corrections yet; read the uploaded dataset's rows from dataset-service
            # (real dependency — raises on failure, never fabricates rows).
            fetched = await self.d.dataset_reader.read_rows(tenant_id, ds_urn)
            rows = [dict(r) for r in fetched]
        cols = [c for c in (rows[0].keys() if rows else []) if c != label_column]
        return rows, cols

    def _first_read_dataset(self, definition) -> str | None:
        for node in definition.get("nodes", []):
            if node.get("component") in ("read-from-warehouse",
                                         "batch-read-from-warehouse"):
                return (node.get("parameters") or {}).get("dataset")
        return None

    async def _finish_success(self, ctx, run_id, result):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            prev = run.status
            run.status = int(RunStatus.succeeded)
            run.finished_at = self.d.clock.now()
            run.model_uri = result.model_uri
            run.metrics = result.metrics
            run.output_dataset_urns = [
                f"wr:{ctx.tenant_id}:model:model/{result.registered_model_name}"
                f"/{result.model_version}"]
            run.components_status = {"train-1": {"alias": "train-1", "phase": "Succeeded",
                                                 "finished_at": run.finished_at.isoformat(),
                                                 "exit_code": 0}}
            await uow.runs.update(run)
            duration = (run.finished_at - (run.started_at or run.finished_at)).total_seconds()
            await self._emit_run(uow, ctx, "pipeline.run.succeeded", run, {
                "mlflow_run_id": result.mlflow_run_id, "duration_s": duration,
                "model_uri": result.model_uri, "metrics": result.metrics})
            await self._emit_status_changed(uow, ctx, run, prev)
            await self._emit_run(uow, ctx, "pipeline.run.output_registered", run, {
                "dataset_urn": run.output_dataset_urns[0],
                "registered_model_name": result.registered_model_name,
                "model_version": result.model_version, "output_name": "model"})
        await self.d.mlflow.set_terminated(result.mlflow_run_id, "FINISHED")

    async def _finish_failure(self, ctx, run_id, exc):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            prev = run.status
            run.status = int(RunStatus.failed)
            run.finished_at = self.d.clock.now()
            code = getattr(exc, "code", "TRAINING_FAILED")
            run.error = {"code": code, "alias": "train-1", "message": str(exc)[:500]}
            run.components_status = {"train-1": {"alias": "train-1", "phase": "Failed",
                                                 "exit_code": 1, "message": str(exc)[:200]}}
            await uow.runs.update(run)
            duration = (run.finished_at - (run.started_at or run.finished_at)).total_seconds()
            await self._emit_run(uow, ctx, "pipeline.run.failed", run, {
                "mlflow_run_id": run.mlflow_run_id, "duration_s": duration,
                "error": run.error})
            await self._emit_status_changed(uow, ctx, run, prev)
            await self._emit_run(uow, ctx, "pipeline.run.outputs_invalidated", run,
                                 {"dataset_urns": run.output_dataset_urns})
        if run.mlflow_run_id:
            await self.d.mlflow.set_terminated(run.mlflow_run_id, "FAILED")

    async def _dequeue_next(self, ctx):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            quota = await uow.quotas.get(ctx.tenant_id)
            limit = quota.max_concurrent_runs if quota else (
                self.d.settings.default_max_concurrent_runs)
            active = await uow.runs.count_active(ctx.tenant_id)
            if active >= limit:
                return None
            run_id = await uow.run_queue.dequeue_next(ctx.tenant_id)
            if run_id is None:
                return None
            run = await uow.runs.get(run_id)
            prev = run.status
            run.status = int(RunStatus.submitted)
            run.submitted_at = self.d.clock.now()
            await uow.runs.update(run)
            await self._emit_run(uow, ctx, "pipeline.run.quota_dequeued", run,
                                 {"queued_ms": 0})
            await self._emit_run(uow, ctx, "pipeline.run.submitted", run, {
                "template_id": run.template_id, "version_id": run.version_id,
                "mlflow_run_id": run.mlflow_run_id,
                "argo_workflow_name": run.argo_workflow_name})
            _ = prev
        return run_id

    async def terminate(self, ctx: CallCtx, run_id: str):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise NotFound("run not found")
            if run.status in (int(RunStatus.succeeded), int(RunStatus.failed),
                              int(RunStatus.cancelled)):
                return run  # idempotent (BR-6, AC-6)
            prev = run.status
            run.status = int(RunStatus.cancelled)
            run.finished_at = self.d.clock.now()
            await uow.run_queue.remove(run_id)
            await uow.runs.update(run)
            duration = (run.finished_at
                        - (run.started_at or run.created_at)).total_seconds()
            await self._emit_run(uow, ctx, "pipeline.run.cancelled", run,
                                 {"cancelled_by": ctx.actor.get("id"),
                                  "duration_s": duration})
            await self._emit_status_changed(uow, ctx, run, prev)
        if run.mlflow_run_id:
            await self.d.mlflow.set_terminated(run.mlflow_run_id, "KILLED")
        return run

    async def retry(self, ctx: CallCtx, run_id: str):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise NotFound("run not found")
            if run.status != int(RunStatus.failed):
                raise Conflict("only failed runs can be retried")
            template_id = run.template_id
            params = dict(run.run_parameters or {})
        return await self.create_run(ctx, template_id, params, retried_from=run_id)

    async def record_component_error(self, tenant_id, argo_workflow_name, body) -> bool:
        """PIPE-FR-036: a component reports a structured exception; store it on the
        run's error field with special-case enrichment (COMPONENT_TIMEOUT / OOM)."""
        async with self.d.uow_factory(tenant_id) as uow:
            run = await uow.runs.get_by_workflow(argo_workflow_name)
            if run is None:
                return False
            detail = body.get("detail", "")
            code = "COMPONENT_ERROR"
            if "longer than the specified deadline" in detail:
                code = "COMPONENT_TIMEOUT"
            elif "OOMKilled" in detail or "OutOfMemory" in detail:
                code = "OUT_OF_MEMORY"
            run.error = {"code": code, "title": body.get("title"), "detail": detail,
                         "alias": body.get("alias"), "source": body.get("source")}
            await uow.runs.update(run)
        return True

    async def get(self, ctx, run_id):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise NotFound("run not found")
        return run

    async def list(self, ctx, filters: RunFilters, limit, cursor):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            return await uow.runs.list(filters, limit, cursor)

    async def get_manifest(self, ctx, run_id):
        async with self.d.uow_factory(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise NotFound("run not found")
            version = await uow.versions.get_by_id(run.version_id)
            manifest = None
            if version and version.compiled_manifest_ref:
                manifest = await self.d.manifest_store.get(version.compiled_manifest_ref)
        return run, manifest, self._resolved_params(run)

    def _resolved_params(self, run) -> dict:
        redacted = {}
        for k, v in (run.run_parameters or {}).items():
            redacted[k] = "***" if "secret" in k.lower() or "token" in k.lower() else v
        redacted["mlflow_run_id"] = run.mlflow_run_id
        return redacted

    async def _emit_run(self, uow, ctx, event_type, run, extra):
        payload = {"run_id": run.id, "status": RunStatus(run.status).name}
        payload.update(extra or {})
        env = make_envelope(
            event_type=event_type, tenant_id=ctx.tenant_id, actor=ctx.actor,
            via_agent=run.via_agent, resource_urn=run_urn(ctx.tenant_id, run.id),
            payload=payload, trace_id=ctx.trace_id)
        await uow.outbox.add(self.d.events_topic, env)

    async def _emit_status_changed(self, uow, ctx, run, previous_status):
        import hashlib
        import json as _json

        digest = hashlib.sha256(
            _json.dumps(run.components_status, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        await self._emit_run(uow, ctx, "pipeline.run.status_changed", run, {
            "previous_status": RunStatus(previous_status).name,
            "components_status_digest": digest})


class AdminService:
    def __init__(self, deps: ServiceDeps):
        self.d = deps

    async def get_quota(self, ctx, tenant_id):
        async with self.d.uow_factory(tenant_id) as uow:
            return await uow.quotas.get(tenant_id)

    async def set_quota(self, ctx, tenant_id, body):
        async with self.d.uow_factory(tenant_id) as uow:
            existing = await uow.quotas.get(tenant_id) or TenantQuota(tenant_id=tenant_id)
            for k in ("max_concurrent_runs", "max_concurrent_pods",
                      "max_run_duration_minutes", "min_seconds_between_runs", "node_pool"):
                if body.get(k) is not None:
                    setattr(existing, k, body[k])
            if body.get("resource_ceiling"):
                existing.resource_ceiling = body["resource_ceiling"]
            await uow.quotas.upsert(existing)
            return existing


class AlgorithmInstantiationService:
    def __init__(self, deps: ServiceDeps, template_service: TemplateService):
        self.d = deps
        self.templates = template_service

    async def instantiate_pipeline(self, ctx, algo_name, *, mode, dataset_refs,
                                   params, workspace_id, name=None):
        algo = self.d.algorithms.get(algo_name)
        if algo is None:
            raise NotFound(f"algorithm template {algo_name!r} not found")
        definition = instantiate(algo, mode=mode, dataset_refs=dataset_refs,
                                 params=params)
        body = {
            "workspace_id": workspace_id,
            "name": name or f"{algo.label} {mode} pipeline {new_id()[:6]}",
            "pipeline_type": "training",
            "model_type": ModelType(algo.model_type).name,
            "algorithm_template_name": algo.name,
            "definition": definition,
            "run_parameters": {"algorithm": algo.name, "label_column":
                               params.get("label_column", "label") if params else "label"},
        }
        return await self.templates.create(ctx, body)
