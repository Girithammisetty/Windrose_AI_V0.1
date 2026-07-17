"""Application services: orchestration of repos, MLflow, and the outbox.

Every mutation writes its event to the outbox inside the same unit of work
(MASTER-FR-034). Reads are served entirely from the Postgres mirror — no MLflow
call in any read path (NFR §9). The only synchronous MLflow writes are
experiment creation (EXP-FR-001) and run-create forwarding.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.domain import card as card_mod
from app.domain.compare import build_comparison
from app.domain.entities import (
    PROMOTION_STATUS,
    RUN_STATUS,
    RUN_STATUS_LABELS,
    STAGE,
    STAGE_LABELS,
    Experiment,
    ModelCard,
    ModelVersion,
    Promotion,
    RegisteredModel,
    Run,
    RunArtifact,
    RunMetric,
    RunParam,
    RunTag,
    model_type_code,
)
from app.domain.errors import (
    Conflict,
    DependencyUnavailable,
    ModelTypeMismatch,
    NotFound,
    RunNotFinished,
    SelfApprovalForbidden,
    ValidationFailed,
)
from app.domain.hidden import is_hidden_param
from app.domain.state import (
    PIPELINE_EVENT_STATUS,
    can_transition_run,
    validate_stage_transition,
)
from app.domain.urn import (
    experiment_urn,
    model_urn,
    model_version_urn,
    promotion_urn,
    run_urn,
)
from app.events.envelope import make_envelope
from app.utils import decode_cursor, json_size_bytes, uuid7

logger = logging.getLogger(__name__)

_MLFLOW_STATUS_TO_RUN = {
    "SCHEDULED": RUN_STATUS["scheduled"], "RUNNING": RUN_STATUS["running"],
    "FINISHED": RUN_STATUS["finished"], "FAILED": RUN_STATUS["failed"],
    "KILLED": RUN_STATUS["killed"],
}

# metric-chart artifacts whose content is served via signed URLs (V1 catalog kept)
METRIC_CHART_ARTIFACTS = ("confusion_matrix", "roc_curve", "decision_tree")

# our stage code -> MLflow model-registry stage string (EXP-FR-032 sync)
STAGE_TO_MLFLOW = {0: "None", 1: "Staging", 2: "Production", 3: "Archived"}

# cap for comma-separated IN-filter batches (bff dataloader N+1 batching)
MAX_BATCH_IDS = 200


@dataclass(slots=True)
class CallCtx:
    tenant_id: str
    actor: dict
    via_agent: dict | None = None
    trace_id: str | None = None
    workspace_id: str | None = None

    @property
    def actor_id(self) -> str:
        return self.actor.get("id", "unknown")


@dataclass(slots=True)
class ServiceDeps:
    settings: Settings
    clock: object
    uow_factory: Callable
    mlflow: object
    artifact_signer: object = field(default=None)


class _Base:
    def __init__(self, deps: ServiceDeps):
        self.deps = deps
        self.settings = deps.settings
        self.clock = deps.clock

    def uow(self, tenant_id: str, *, worker: bool = False):
        return self.deps.uow_factory(tenant_id, worker=worker)

    async def _emit(self, uow, ctx: CallCtx, event_type: str, resource_urn: str,
                    payload: dict, *, trace_id: str | None = None) -> None:
        await uow.outbox.add(
            self.settings.events_topic,
            make_envelope(
                event_type=event_type, tenant_id=ctx.tenant_id, actor=ctx.actor,
                via_agent=ctx.via_agent, resource_urn=resource_urn, payload=payload,
                trace_id=trace_id or ctx.trace_id,
            ),
        )


def _ms_to_dt(ms) -> datetime | None:
    if ms in (None, 0):
        return None
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=UTC)


# ---------------------------------------------------------------------------
# Experiments (EXP-FR-001/002)
# ---------------------------------------------------------------------------


class ExperimentService(_Base):
    async def create(self, ctx: CallCtx, payload: dict) -> Experiment:
        model_type = model_type_code(payload["model_type"])
        pipes = {
            "model_pipeline_urn": payload["model_pipeline_urn"],
            "feature_engineering_pipeline_urn": payload["feature_engineering_pipeline_urn"],
            "training_pipeline_urn": payload["training_pipeline_urn"],
        }
        if any(not v for v in pipes.values()):
            raise ValidationFailed("all three pipeline URNs are required (EXP-FR-001)")
        if len(set(pipes.values())) != 3:
            raise ValidationFailed("the three pipeline URNs must be mutually distinct")

        now = self.clock.now()
        workspace_id = payload["workspace_id"]
        name = payload["name"]
        async with self.uow(ctx.tenant_id) as uow:
            if await uow.experiments.get_by_name(workspace_id, name):
                raise Conflict(f"experiment name {name!r} already exists in workspace")

        # EXP-FR-001: the only permitted synchronous MLflow write on create.
        # BR-8: MLflow down -> 503 (mlflow_experiment_id is mandatory).
        try:
            mlflow_experiment_id = await self.deps.mlflow.create_experiment(
                f"{ctx.tenant_id}/{workspace_id}/{name}",
                tags={"windrose_tenant": ctx.tenant_id, "windrose_workspace": workspace_id},
            )
        except DependencyUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailable(f"MLflow experiment create failed: {exc}") from exc

        exp = Experiment(
            id=str(uuid7()), tenant_id=ctx.tenant_id, workspace_id=workspace_id, name=name,
            model_type=model_type, mlflow_experiment_id=mlflow_experiment_id,
            model_pipeline_urn=pipes["model_pipeline_urn"],
            feature_engineering_pipeline_urn=pipes["feature_engineering_pipeline_urn"],
            training_pipeline_urn=pipes["training_pipeline_urn"],
            description=payload.get("description"), note=payload.get("note"),
            tags=payload.get("tags") or {}, created_by=ctx.actor_id,
            created_at=now, updated_at=now,
        )
        async with self.uow(ctx.tenant_id) as uow:
            if await uow.experiments.get_by_name(workspace_id, name):
                raise Conflict(f"experiment name {name!r} already exists in workspace")
            await uow.experiments.add(exp)
            await uow.watermarks.upsert(mlflow_experiment_id, ctx.tenant_id, now)
            await self._emit(uow, ctx, "experiment.created", experiment_urn(ctx.tenant_id, exp.id),
                             {"experiment_id": exp.id, "name": name, "model_type": model_type,
                              "workspace_id": workspace_id})
            await uow.commit()
        return exp

    async def get(self, ctx: CallCtx, exp_id: str) -> Experiment:
        async with self.uow(ctx.tenant_id) as uow:
            exp = await uow.experiments.get(exp_id)
            if not exp:
                raise NotFound("experiment not found")
            return exp

    async def list(self, ctx: CallCtx, workspace_id: str | None, limit: int,
                   cursor: str | None, archived: bool = False):
        async with self.uow(ctx.tenant_id) as uow:
            return await uow.experiments.list(workspace_id, archived, limit, cursor)

    async def patch(self, ctx: CallCtx, exp_id: str, changes: dict) -> Experiment:
        async with self.uow(ctx.tenant_id) as uow:
            exp = await uow.experiments.get(exp_id)
            if not exp:
                raise NotFound("experiment not found")
            if "name" in changes and changes["name"] != exp.name:
                if await uow.experiments.get_by_name(exp.workspace_id, changes["name"]):
                    raise Conflict("experiment name already exists in workspace")
            for key in ("name", "description", "note", "tags"):
                if key in changes and changes[key] is not None:
                    setattr(exp, key, changes[key])
            exp.updated_at = self.clock.now()
            await uow.experiments.update(exp)
            await self._emit(uow, ctx, "experiment.updated",
                             experiment_urn(ctx.tenant_id, exp.id),
                             {"experiment_id": exp.id, "name": exp.name,
                              "model_type": exp.model_type, "workspace_id": exp.workspace_id})
            await uow.commit()
        return exp

    async def archive(self, ctx: CallCtx, exp_id: str) -> Experiment:
        async with self.uow(ctx.tenant_id) as uow:
            exp = await uow.experiments.get(exp_id)
            if not exp:
                raise NotFound("experiment not found")
            exp.deleted_at = self.clock.now()
            exp.updated_at = exp.deleted_at
            await uow.experiments.update(exp)
            await self._emit(uow, ctx, "experiment.archived",
                             experiment_urn(ctx.tenant_id, exp.id),
                             {"experiment_id": exp.id, "name": exp.name,
                              "model_type": exp.model_type, "workspace_id": exp.workspace_id})
            await uow.commit()
        # mirror to MLflow as tag (best-effort; mirror stays authoritative on failure)
        try:
            await self.deps.mlflow.set_experiment_tag(exp.mlflow_experiment_id, "archived", "true")
        except Exception:  # noqa: BLE001
            pass
        return exp

    async def restore(self, ctx: CallCtx, exp_id: str) -> Experiment:
        async with self.uow(ctx.tenant_id) as uow:
            exp = await uow.experiments.get(exp_id, include_deleted=True)
            if not exp:
                raise NotFound("experiment not found")
            if not exp.deleted_at:
                raise Conflict("experiment is not archived")
            name = exp.name
            while await uow.experiments.get_by_name(exp.workspace_id, name):
                name = f"Copy of {name}"
            exp.name = name
            exp.deleted_at = None
            exp.updated_at = self.clock.now()
            await uow.experiments.update(exp)
            await self._emit(uow, ctx, "experiment.restored",
                             experiment_urn(ctx.tenant_id, exp.id),
                             {"experiment_id": exp.id, "name": exp.name,
                              "model_type": exp.model_type, "workspace_id": exp.workspace_id})
            await uow.commit()
        try:
            await self.deps.mlflow.set_experiment_tag(
                exp.mlflow_experiment_id, "archived", "false")
        except Exception:  # noqa: BLE001
            pass
        return exp


# ---------------------------------------------------------------------------
# Runs (mirror) — EXP-FR-003/004/005/006
# ---------------------------------------------------------------------------


class RunService(_Base):
    async def create_from_pipeline(self, ctx: CallCtx, payload: dict) -> Run | None:
        """Consume pipeline.run.submitted: insert run row (scheduled) linked by
        mlflow_run_id + experiment; emit run.mirrored (AC-1)."""
        mlflow_run_id = payload["mlflow_run_id"]
        async with self.uow(ctx.tenant_id) as uow:
            existing = await uow.runs.get_by_mlflow_run_id(mlflow_run_id)
            if existing:
                return existing  # idempotent
            experiment = await self._resolve_experiment(uow, payload)
            if experiment is None:
                raise NotFound("experiment for run not found")
            now = self.clock.now()
            run = Run(
                id=str(uuid7()), tenant_id=ctx.tenant_id, experiment_id=experiment.id,
                mlflow_run_id=mlflow_run_id, status=RUN_STATUS["scheduled"],
                name=payload.get("name"), algorithm=payload.get("algorithm", ""),
                pipeline_run_urn=payload.get("pipeline_run_urn"),
                input_dataset_urns=payload.get("input_dataset_urns") or [],
                created_by=ctx.actor_id, created_at=now, updated_at=now,
            )
            await uow.runs.add(run)
            await self._emit(uow, ctx, "run.mirrored", run_urn(ctx.tenant_id, run.id),
                             {"run_id": run.id, "mlflow_run_id": mlflow_run_id,
                              "experiment_id": experiment.id})
            await uow.commit()
        return run

    async def _resolve_experiment(self, uow, payload: dict):
        if payload.get("experiment_id"):
            exp = await uow.experiments.get(payload["experiment_id"])
            if exp:
                return exp
        if payload.get("mlflow_experiment_id"):
            return await uow.experiments.get_by_mlflow_id(payload["mlflow_experiment_id"])
        return None

    async def transition_status(self, ctx: CallCtx, event_type: str, payload: dict) -> Run | None:
        """Run status transitions come from pipeline events (EXP-FR-003)."""
        target = PIPELINE_EVENT_STATUS.get(event_type)
        if target is None:
            return None
        mlflow_run_id = payload["mlflow_run_id"]
        async with self.uow(ctx.tenant_id) as uow:
            run = await uow.runs.get_by_mlflow_run_id(mlflow_run_id, include_deleted=False)
            if run is None:
                # race with submitted; caller (consumer) leaves event to retry
                raise NotFound("run not yet mirrored")
            if not can_transition_run(run.status, target):
                return run
            previous = run.status
            run.status = target
            if payload.get("duration_ms") is not None:
                run.duration_ms = payload["duration_ms"]
            if payload.get("error_messages"):
                run.error_messages = payload["error_messages"]
            if target == RUN_STATUS["running"] and run.started_at is None:
                run.started_at = self.clock.now()
            if target in (RUN_STATUS["finished"], RUN_STATUS["failed"], RUN_STATUS["killed"]):
                run.ended_at = self.clock.now()
            run.updated_at = self.clock.now()
            await uow.runs.update(run)
            await self._emit(uow, ctx, "run.status_changed", run_urn(ctx.tenant_id, run.id),
                             {"run_id": run.id, "status": target, "previous_status": previous})
            await uow.commit()
        return run

    async def append_output_dataset(self, ctx: CallCtx, payload: dict) -> None:
        """pipeline.run.output_registered -> append dataset URN to run refs."""
        mlflow_run_id = payload["mlflow_run_id"]
        urn = payload.get("dataset_urn")
        if not urn:
            return
        async with self.uow(ctx.tenant_id) as uow:
            run = await uow.runs.get_by_mlflow_run_id(mlflow_run_id, include_deleted=False)
            if run is None:
                raise NotFound("run not yet mirrored")
            kind = payload.get("kind", "input")
            target = run.input_dataset_urns if kind == "input" else run.output_dataset_urns
            if urn not in target:
                target.append(urn)
                run.updated_at = self.clock.now()
                await uow.runs.update(run)
            await uow.commit()

    async def get_detail(self, ctx: CallCtx, run_id: str, include_hidden: bool = False) -> dict:
        async with self.uow(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if not run:
                raise NotFound("run not found")
            params = await uow.runs.get_params(run_id)
            metrics = await uow.runs.get_metrics(run_id)
            tags = await uow.runs.get_tags(run_id)
            artifacts = await uow.runs.get_artifacts(run_id)
            note = await uow.runs.get_note(run_id)
        visible_params = {
            p.key: p.value for p in params if include_hidden or not p.is_hidden
        }
        return {
            "run": _run_payload(ctx, run),
            "params": visible_params,
            "params_conflict": [p.key for p in params if p.param_conflict],
            "metrics": {m.key: {"value": m.value, "step": m.step,
                                "logged_at": m.logged_at.isoformat()} for m in metrics},
            "tags": {t.key: t.value for t in tags},
            "artifacts": [{"path": a.path, "size_bytes": a.size_bytes,
                           "content_type": a.content_type} for a in artifacts],
            "input_dataset_urns": run.input_dataset_urns,
            "output_dataset_urns": run.output_dataset_urns,
            "note": note,
        }

    async def list(self, ctx: CallCtx, experiment_id: str, limit: int, cursor: str | None):
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.experiments.get(experiment_id):
                raise NotFound("experiment not found")
            return await uow.runs.list_by_experiment(experiment_id, limit, cursor)

    async def update(self, ctx: CallCtx, run_id: str, changes: dict) -> dict:
        async with self.uow(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if not run:
                raise NotFound("run not found")
            if "name" in changes and changes["name"] is not None:
                run.name = changes["name"]
            if "note" in changes and changes["note"] is not None:
                await uow.runs.set_note(run_id, ctx.tenant_id, changes["note"])
            if "tags" in changes and changes["tags"] is not None:
                for k, v in changes["tags"].items():
                    await uow.runs.upsert_tag(RunTag(run_id, ctx.tenant_id, k, str(v)))
            run.updated_at = self.clock.now()
            await uow.runs.update(run)
            await self._emit(uow, ctx, "run.status_changed", run_urn(ctx.tenant_id, run.id),
                             {"run_id": run.id, "status": run.status,
                              "previous_status": run.status})
            await uow.commit()
        return await self.get_detail(ctx, run_id)

    async def set_note(self, ctx: CallCtx, run_id: str, description: str) -> str:
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.runs.get(run_id):
                raise NotFound("run not found")
            await uow.runs.set_note(run_id, ctx.tenant_id, description)
            await uow.commit()
        return description

    async def get_note(self, ctx: CallCtx, run_id: str) -> str:
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.runs.get(run_id):
                raise NotFound("run not found")
            note = await uow.runs.get_note(run_id)
            if note is None:
                raise NotFound("run has no note")
            return note

    async def delete_note(self, ctx: CallCtx, run_id: str) -> None:
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.runs.get(run_id):
                raise NotFound("run not found")
            await uow.runs.delete_note(run_id)
            await uow.commit()

    async def delete(self, ctx: CallCtx, run_id: str) -> None:
        async with self.uow(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if not run:
                raise NotFound("run not found")
            run.deleted_at = self.clock.now()
            run.updated_at = run.deleted_at
            await uow.runs.update(run)
            await self._emit(uow, ctx, "run.status_changed", run_urn(ctx.tenant_id, run.id),
                             {"run_id": run.id, "status": run.status, "deleted": True})
            await uow.commit()
        # tombstone the MLflow run (best-effort; the mirror stays authoritative)
        try:
            await self.deps.mlflow.delete_run(run.mlflow_run_id)
        except Exception:  # noqa: BLE001
            pass

    async def artifacts(self, ctx: CallCtx, run_id: str) -> list[dict]:
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.runs.get(run_id):
                raise NotFound("run not found")
            arts = await uow.runs.get_artifacts(run_id)
        return [{"path": a.path, "size_bytes": a.size_bytes, "content_type": a.content_type}
                for a in arts]

    async def artifact_url(self, ctx: CallCtx, run_id: str, path: str) -> str:
        async with self.uow(ctx.tenant_id) as uow:
            run = await uow.runs.get(run_id)
            if not run:
                raise NotFound("run not found")
            arts = {a.path for a in await uow.runs.get_artifacts(run_id)}
            if path not in arts:
                raise NotFound("artifact not found")
        if not run.artifact_uri:
            raise NotFound("run has no artifact_uri")
        return await self.deps.artifact_signer.signed_url(
            run.artifact_uri, path, self.settings.signed_url_ttl_seconds)


def _run_payload(ctx: CallCtx, run: Run) -> dict:
    from app.domain.entities import RUN_UI_LABELS

    return {
        "id": run.id, "urn": run_urn(ctx.tenant_id, run.id),
        "experiment_id": run.experiment_id, "mlflow_run_id": run.mlflow_run_id,
        "name": run.name, "status": RUN_STATUS_LABELS[run.status],
        "status_label": RUN_UI_LABELS[run.status], "algorithm": run.algorithm,
        "artifact_uri": run.artifact_uri, "duration_ms": run.duration_ms,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "error_messages": run.error_messages,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


# ---------------------------------------------------------------------------
# Mirror application (webhook inbox + reconciliation) — EXP-FR-011/012/013/014
# ---------------------------------------------------------------------------


class MirrorService(_Base):
    async def ingest_webhook(self, *, tenant_id: str, delivery_id: str, event_type: str,
                             payload: dict) -> bool:
        """EXP-FR-011: ingest-only. Dedup on delivery_id; returns True if newly
        parked (204 either way). Application happens asynchronously."""
        async with self.uow(tenant_id) as uow:
            inserted = await uow.inbox.add(delivery_id=delivery_id, tenant_id=tenant_id,
                                           event_type=event_type, payload=payload)
            await uow.commit()
        return inserted

    async def apply_inbox_once(self, tenant_id: str, limit: int = 100) -> int:
        applied = 0
        async with self.uow(tenant_id) as uow:
            rows = await uow.inbox.unapplied(limit)
        for row in rows:
            try:
                await self._apply_event(tenant_id, row.event_type, row.payload)
                async with self.uow(tenant_id) as uow:
                    await uow.inbox.mark_applied(row.delivery_id)
                    await uow.commit()
                applied += 1
            except NotFound as exc:
                # BR-2: webhook before run row exists -> leave parked, retried later
                async with self.uow(tenant_id) as uow:
                    await uow.inbox.mark_error(row.delivery_id, str(exc))
                    await uow.commit()
        return applied

    async def _apply_event(self, tenant_id: str, event_type: str, payload: dict) -> None:
        ctx = CallCtx(tenant_id=tenant_id,
                      actor={"type": "service", "id": "mlflow-webhook"})
        if event_type in ("run.updated", "run.created"):
            await self._apply_run_data(ctx, payload)
        elif event_type in ("model_version.created", "model_version.tag.set",
                             "registered_model.created"):
            # registry mirror events are advisory; the authoritative registry
            # write path is the register endpoint. Acknowledge (no-op mirror).
            return
        else:
            return

    async def _apply_run_data(self, ctx: CallCtx, payload: dict) -> bool:
        """Upsert metrics/params/tags for an existing run (EXP-FR-012).
        Returns True if the mirror changed. Raises NotFound if the run row does
        not exist yet (BR-2 park-and-retry)."""
        mlflow_run_id = payload["run_id"] if "run_id" in payload else payload["mlflow_run_id"]
        data = payload.get("data", payload)
        changed = False
        keys_touched: list[str] = []
        async with self.uow(ctx.tenant_id) as uow:
            run = await uow.runs.get_by_mlflow_run_id(mlflow_run_id, include_deleted=False)
            if run is None:
                raise NotFound(f"run {mlflow_run_id} not yet mirrored")
            for m in data.get("metrics", []):
                logged_at = _ms_to_dt(m.get("timestamp")) or self.clock.now()
                existing = {x.key: x for x in await uow.runs.get_metrics(run.id)}
                cur = existing.get(m["key"])
                metric = RunMetric(run.id, ctx.tenant_id, m["key"], float(m["value"]),
                                   int(m.get("step", 0)), logged_at)
                if cur is None or cur.value != metric.value:
                    changed = True
                    keys_touched.append(m["key"])
                await uow.runs.upsert_metric(metric)
                await uow.runs.append_metric_history(metric)
            for p in data.get("params", []):
                conflict = await uow.runs.upsert_param(
                    RunParam(run.id, ctx.tenant_id, p["key"], str(p["value"]),
                             is_hidden=is_hidden_param(p["key"])))
                changed = changed or conflict
            for t in data.get("tags", []):
                await uow.runs.upsert_tag(RunTag(run.id, ctx.tenant_id, t["key"], str(t["value"])))
                changed = True
            for a in data.get("artifacts", []):
                await uow.runs.upsert_artifact(
                    RunArtifact(run.id, ctx.tenant_id, a["path"], int(a.get("size_bytes", 0)),
                                a.get("content_type")))
            if data.get("artifact_uri") and run.artifact_uri != data["artifact_uri"]:
                run.artifact_uri = data["artifact_uri"]
                await uow.runs.update(run)
                changed = True
            if keys_touched:
                await self._emit(uow, ctx, "run.metrics_updated", run_urn(ctx.tenant_id, run.id),
                                 {"run_id": run.id, "keys": sorted(set(keys_touched))})
            await uow.commit()
        return changed

    async def apply_mlflow_run(self, ctx: CallCtx, mlflow_run: dict) -> bool:
        """Reconciliation repair: insert a missing run or repair its mirror from
        an MLflow run dict. Returns True if the mirror was changed (repaired)."""
        info = mlflow_run.get("info", {})
        data = mlflow_run.get("data", {})
        mlflow_run_id = info.get("run_id") or info.get("run_uuid")
        changed = False
        async with self.uow(ctx.tenant_id) as uow:
            run = await uow.runs.get_by_mlflow_run_id(mlflow_run_id, include_deleted=True)
            if run is None:
                exp = await uow.experiments.get_by_mlflow_id(info.get("experiment_id", ""))
                if exp is None:
                    return False  # run for an experiment we don't own — skip
                now = self.clock.now()
                run = Run(
                    id=str(uuid7()), tenant_id=ctx.tenant_id, experiment_id=exp.id,
                    mlflow_run_id=mlflow_run_id,
                    status=_MLFLOW_STATUS_TO_RUN.get(info.get("status", ""),
                                                     RUN_STATUS["scheduled"]),
                    name=info.get("run_name"), artifact_uri=info.get("artifact_uri"),
                    started_at=_ms_to_dt(info.get("start_time")),
                    ended_at=_ms_to_dt(info.get("end_time")),
                    created_by="reconciliation", created_at=now, updated_at=now,
                )
                await uow.runs.add(run)
                await self._emit(uow, ctx, "run.mirrored", run_urn(ctx.tenant_id, run.id),
                                 {"run_id": run.id, "mlflow_run_id": mlflow_run_id,
                                  "experiment_id": exp.id})
                changed = True
            existing_metrics = {x.key: x for x in await uow.runs.get_metrics(run.id)}
            for m in data.get("metrics", []):
                metric = RunMetric(run.id, ctx.tenant_id, m["key"], float(m["value"]),
                                   int(m.get("step", 0)),
                                   _ms_to_dt(m.get("timestamp")) or self.clock.now())
                cur = existing_metrics.get(m["key"])
                if cur is None or cur.value != metric.value:
                    changed = True
                    await uow.runs.upsert_metric(metric)
                    await uow.runs.append_metric_history(metric)
            existing_params = {x.key for x in await uow.runs.get_params(run.id)}
            for p in data.get("params", []):
                if p["key"] not in existing_params:
                    changed = True
                await uow.runs.upsert_param(
                    RunParam(run.id, ctx.tenant_id, p["key"], str(p["value"]),
                             is_hidden=is_hidden_param(p["key"])))
                if p["key"] == "algorithm" and run.algorithm != str(p["value"]):
                    run.algorithm = str(p["value"])
                    await uow.runs.update(run)
            existing_tags = {x.key: x.value for x in await uow.runs.get_tags(run.id)}
            for t in data.get("tags", []):
                if existing_tags.get(t["key"]) != str(t["value"]):
                    changed = True
                await uow.runs.upsert_tag(RunTag(run.id, ctx.tenant_id, t["key"], str(t["value"])))
            await uow.commit()
        return changed


class ReconciliationService(_Base):
    """EXP-FR-013: reconciliation sweep against real MLflow — the safety net that
    repairs missed webhooks. Steady-state drift must be ~0."""

    def __init__(self, deps: ServiceDeps, mirror: MirrorService):
        super().__init__(deps)
        self.mirror = mirror

    async def sweep_tenant(self, tenant_id: str) -> dict:
        """``drift_count`` = runs found out-of-sync with MLflow this sweep;
        ``repaired_count`` = runs the sweep successfully wrote a fix for. In
        steady state both are 0; the 3-consecutive-sweep alert keys on
        ``drift_count`` (EXP-FR-013). They diverge if a repair write fails (drift
        detected but not repaired that sweep)."""
        ctx = CallCtx(tenant_id=tenant_id, actor={"type": "service", "id": "reconciliation"})
        drift_count = 0
        repaired_count = 0
        swept: list[str] = []
        async with self.uow(tenant_id) as uow:
            experiments = await uow.experiments.all_active()
        mlflow_ids = [e.mlflow_experiment_id for e in experiments]
        if not mlflow_ids:
            return {"repaired_count": 0, "drift_count": 0, "swept_experiments": []}
        for mlflow_id in mlflow_ids:
            page_token = None
            while True:
                runs, page_token = await self.deps.mlflow.search_runs(
                    [mlflow_id], max_results=self.settings.reconcile_page_size,
                    page_token=page_token)
                for mlflow_run in runs:
                    try:
                        if await self.mirror.apply_mlflow_run(ctx, mlflow_run):
                            drift_count += 1
                            repaired_count += 1
                    except Exception:  # noqa: BLE001 — drift seen but repair failed
                        drift_count += 1
                        logger.exception("reconciliation repair failed for a run")
                if not page_token:
                    break
            swept.append(mlflow_id)
            async with self.uow(tenant_id) as uow:
                await uow.watermarks.upsert(mlflow_id, tenant_id, self.clock.now())
                await uow.commit()
        async with self.uow(tenant_id) as uow:
            await self._emit(uow, ctx, "experiment.mirror.reconciled",
                             experiment_urn(tenant_id, "sweep"),
                             {"tenant_id": tenant_id, "repaired_count": repaired_count,
                              "drift_count": drift_count, "swept_experiments": swept})
            await uow.commit()
        return {"repaired_count": repaired_count, "drift_count": drift_count,
                "swept_experiments": swept}


# ---------------------------------------------------------------------------
# Comparison + query (EXP-FR-020/021/050/051)
# ---------------------------------------------------------------------------


class CompareService(_Base):
    async def compare(self, ctx: CallCtx, *, run_ids: list[str], metrics: list[str] | None,
                      params: list[str] | None, include_all: bool, cursor: str | None) -> dict:
        if len(run_ids) != len(set(run_ids)):
            raise ValidationFailed("duplicate run ids are not allowed (BR-9)")
        if len(run_ids) < self.settings.compare_min_runs:
            raise ValidationFailed(f"compare requires >= {self.settings.compare_min_runs} runs")
        if len(run_ids) > self.settings.compare_max_runs:
            raise ValidationFailed(f"compare capped at {self.settings.compare_max_runs} runs")
        async with self.uow(ctx.tenant_id) as uow:
            found = await uow.runs.get_many(run_ids)
            found_ids = {r.id for r in found}
            if found_ids != set(run_ids):
                # BR-9: any non-visible run makes the whole request 404
                raise NotFound("one or more runs are not visible in this workspace")
            metric_rows = await uow.runs.metrics_for_runs(run_ids)
            param_rows = await uow.runs.params_for_runs(run_ids)
        offset = int(decode_cursor(cursor).get("o", 0)) if cursor else 0
        result = build_comparison(
            run_ids=run_ids, metric_rows=metric_rows, param_rows=param_rows,
            requested_metrics=metrics, requested_params=params, include_all=include_all,
            loss_prefixes=self.settings.loss_metric_prefixes,
            page_size=self.settings.compare_default_page_size, offset=offset)
        return {
            "runs": result.run_ids, "metrics": result.metrics, "params": result.params,
            "next_cursor": result.next_cursor, "has_more": result.has_more,
        }

    async def metric_history(self, ctx: CallCtx, run_id: str, keys: list[str] | None,
                             limit: int, cursor: str | None):
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.runs.get(run_id):
                raise NotFound("run not found")
            return await uow.runs.metric_history(run_id, keys, limit, cursor)


class QueryService(_Base):
    async def search_runs(self, ctx: CallCtx, *, experiment_ids: list[str] | None,
                          status: str | None, algorithm: str | None, tag: str | None,
                          metric_predicates: list[tuple[str, str, float]],
                          param_predicates: list[tuple[str, str]], sort: str,
                          limit: int, cursor: str | None):
        if experiment_ids is not None and len(experiment_ids) > MAX_BATCH_IDS:
            raise ValidationFailed(f"at most {MAX_BATCH_IDS} experiment ids per query")
        if len(metric_predicates) > self.settings.query_max_metric_predicates:
            raise ValidationFailed(
                f"at most {self.settings.query_max_metric_predicates} metric predicates")
        if len(param_predicates) > self.settings.query_max_param_predicates:
            raise ValidationFailed(
                f"at most {self.settings.query_max_param_predicates} param predicates")
        status_code = None
        if status:
            if status not in RUN_STATUS:
                raise ValidationFailed(f"unknown status {status!r}")
            status_code = RUN_STATUS[status]
        tag_tuple = None
        if tag:
            if ":" not in tag:
                raise ValidationFailed("tag filter must be key:value")
            k, v = tag.split(":", 1)
            tag_tuple = (k, v)
        async with self.uow(ctx.tenant_id) as uow:
            return await uow.runs.search(
                experiment_ids=experiment_ids, status=status_code, algorithm=algorithm,
                tag=tag_tuple, metric_predicates=metric_predicates,
                param_predicates=param_predicates, sort=sort, limit=limit, cursor=cursor)

    async def best_run(self, ctx: CallCtx, experiment_id: str, metric: str, direction: str,
                       status: str | None) -> dict:
        if direction not in ("max", "min"):
            raise ValidationFailed("direction must be max or min")
        status_code = RUN_STATUS[status] if status in RUN_STATUS else None
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.experiments.get(experiment_id):
                raise NotFound("experiment not found")
            run = await uow.runs.best(experiment_id, metric, direction, status_code)
            if run is None:
                raise NotFound(f"no run with metric {metric!r} in experiment")
            metrics = {m.key: m.value for m in await uow.runs.get_metrics(run.id)}
        payload = _run_payload(ctx, run)
        payload["metrics"] = metrics
        return payload


# ---------------------------------------------------------------------------
# Registry + promotion (EXP-FR-030..036) + model cards (EXP-FR-040)
# ---------------------------------------------------------------------------


class RegistryService(_Base):
    async def register(self, ctx: CallCtx, experiment_id: str, run_id: str,
                       payload: dict) -> dict:
        model_name = payload["model_name"]
        async with self.uow(ctx.tenant_id) as uow:
            experiment = await uow.experiments.get(experiment_id)
            if not experiment:
                raise NotFound("experiment not found")
            run = await uow.runs.get(run_id)
            if not run or run.experiment_id != experiment_id:
                raise NotFound("run not found")
            if run.status != RUN_STATUS["finished"]:
                raise RunNotFinished("run must be finished to register (EXP-FR-031)")

            model = await uow.models.get_model_by_name(experiment.workspace_id, model_name)
            model_created = False
            if model is None:
                now = self.clock.now()
                model = RegisteredModel(
                    id=str(uuid7()), tenant_id=ctx.tenant_id,
                    workspace_id=experiment.workspace_id, name=model_name,
                    model_type=experiment.model_type,
                    owner_id=payload.get("owner_id") or _as_uuid(ctx.actor_id),
                    description=payload.get("description"), created_by=ctx.actor_id,
                    created_at=now, updated_at=now)
                await uow.models.add_model(model)
                model_created = True
            elif model.model_type != experiment.model_type:
                raise ModelTypeMismatch(
                    "model name exists with a different model_type (BR-12)")

            version_no = await uow.models.next_version_no(model.id)
            now = self.clock.now()
            version = ModelVersion(
                id=str(uuid7()), tenant_id=ctx.tenant_id, model_id=model.id,
                version=version_no, source_run_id=run.id, stage=STAGE["none"],
                mlflow_model_ref=payload.get("mlflow_model_ref") or run.mlflow_run_id,
                flavor=payload.get("flavor") or "mlflow.sklearn",
                input_schema=payload.get("input_schema"),
                output_schema=payload.get("output_schema"),
                stage_updated_at=now, created_by=ctx.actor_id, created_at=now, updated_at=now)
            await uow.models.add_version(version)

            # immutable run snapshot (<= 64KB) into the append-only log
            params = await uow.runs.get_params(run.id)
            metrics = await uow.runs.get_metrics(run.id)
            snapshot = {
                "run_id": run.id, "mlflow_run_id": run.mlflow_run_id,
                "algorithm": run.algorithm, "status": run.status,
                "params": {p.key: p.value for p in params},
                "metrics": {m.key: m.value for m in metrics},
                "input_dataset_urns": run.input_dataset_urns,
            }
            if json_size_bytes(snapshot) > 64 * 1024:
                snapshot = {"run_id": run.id, "mlflow_run_id": run.mlflow_run_id,
                            "truncated": True}
            await uow.models.add_registration_log(
                model_version_id=version.id, experiment_id=experiment.id,
                tenant_id=ctx.tenant_id, run_snapshot=snapshot,
                registered_by=ctx.actor_id, via_agent=ctx.via_agent)

            # auto-generated model card (EXP-FR-040)
            visible_params = {p.key: p.value for p in params if not p.is_hidden}
            final_metrics = {m.key: m.value for m in metrics}
            chart_artifacts = [a.path for a in await uow.runs.get_artifacts(run.id)
                               if any(c in a.path for c in METRIC_CHART_ARTIFACTS)]
            auto = card_mod.build_auto_fields(
                model=model, version=version, experiment=experiment, run=run,
                visible_params=visible_params, final_metrics=final_metrics,
                metric_chart_artifacts=chart_artifacts, promotion_history=[],
                via_agent=ctx.via_agent)
            await uow.models.upsert_card(ModelCard(
                model_version_id=version.id, tenant_id=ctx.tenant_id, auto_fields=auto,
                overlay={}, overlay_version=0, created_at=now, updated_at=now))

            if model_created:
                await self._emit(uow, ctx, "model.created", model_urn(ctx.tenant_id, model.id),
                                 {"model_id": model.id, "name": model.name,
                                  "model_type": model.model_type})
            await self._emit(uow, ctx, "model_version.created",
                             model_version_urn(ctx.tenant_id, model.id, version_no),
                             {"model_id": model.id, "version": version_no,
                              "source_run_id": run.id, "stage": STAGE["none"]})
            await uow.commit()
        return {"model_id": model.id, "version": version_no, "stage": "none",
                "model_created": model_created}

    async def get_model(self, ctx: CallCtx, model_id: str) -> dict:
        async with self.uow(ctx.tenant_id) as uow:
            model = await uow.models.get_model(model_id)
            if not model:
                raise NotFound("model not found")
            versions = await uow.models.list_versions(model_id)
        return {"model": _model_payload(ctx, model),
                "versions": [_version_payload(ctx, v) for v in versions]}

    async def get_version(self, ctx: CallCtx, model_id: str, version: int) -> dict:
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.models.get_model(model_id):
                raise NotFound("model not found")
            v = await uow.models.get_version(model_id, version)
            if not v:
                raise NotFound("model version not found")
        return _version_payload(ctx, v)

    async def list_models(self, ctx: CallCtx, workspace_id: str | None, stage: str | None,
                          limit: int, cursor: str | None, ids: list[str] | None = None):
        if ids is not None and len(ids) > MAX_BATCH_IDS:
            raise ValidationFailed(f"at most {MAX_BATCH_IDS} model ids per query")
        stage_code = STAGE[stage] if stage in STAGE else None
        async with self.uow(ctx.tenant_id) as uow:
            page = await uow.models.list_models(workspace_id, stage_code, limit, cursor, ids=ids)
        page.items = [_model_payload(ctx, m) for m in page.items]
        return page


def _as_uuid(value: str) -> str:
    """Coerce an actor id to a UUID string for owner_id (owner is a UUID column).
    Non-UUID actor ids (test users) map to a deterministic namespace UUID."""
    import uuid

    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"windrose-actor:{value}"))


def _model_payload(ctx: CallCtx, model: RegisteredModel) -> dict:
    from app.domain.entities import MODEL_TYPE_LABELS

    return {"id": model.id, "urn": model_urn(ctx.tenant_id, model.id), "name": model.name,
            "model_type": MODEL_TYPE_LABELS[model.model_type], "owner_id": model.owner_id,
            "description": model.description,
            "created_at": model.created_at.isoformat() if model.created_at else None}


def _version_payload(ctx: CallCtx, v: ModelVersion) -> dict:
    return {"model_id": v.model_id, "version": v.version,
            "urn": model_version_urn(ctx.tenant_id, v.model_id, v.version),
            "source_run_id": v.source_run_id, "stage": STAGE_LABELS[v.stage],
            "mlflow_model_ref": v.mlflow_model_ref, "flavor": v.flavor,
            "input_schema": v.input_schema, "output_schema": v.output_schema,
            "stage_updated_at": v.stage_updated_at.isoformat() if v.stage_updated_at else None}


class PromotionService(_Base):
    async def promote(self, ctx: CallCtx, model_id: str, version: int, payload: dict) -> dict:
        target_stage = payload["target_stage"]
        if target_stage not in STAGE:
            raise ValidationFailed(f"unknown target_stage {target_stage!r}")
        target_code = STAGE[target_stage]
        async with self.uow(ctx.tenant_id) as uow:
            model = await uow.models.get_model(model_id)
            if not model:
                raise NotFound("model not found")
            v = await uow.models.get_version(model_id, version)
            if not v:
                raise NotFound("model version not found")
            if v.deleted_at is not None:
                raise ValidationFailed("model version is soft-deleted")
            # guard: source run must be finished
            run = await uow.runs.get(v.source_run_id)
            if not run or run.status != RUN_STATUS["finished"]:
                raise RunNotFinished("source run must be finished to promote")
            validate_stage_transition(v.stage, target_code)
            if await uow.models.pending_for_version(v.id):
                raise Conflict("a promotion is already pending for this version (BR-4)")

            now = self.clock.now()
            promotion = Promotion(
                id=str(uuid7()), tenant_id=ctx.tenant_id, model_version_id=v.id,
                target_stage=target_code, from_stage=v.stage,
                status=PROMOTION_STATUS["pending"], rationale=payload.get("rationale"),
                requested_by=ctx.actor_id, via_agent=ctx.via_agent,
                workflow_id=f"promotion-{uuid7()}",
                expires_at=now + timedelta(days=self.settings.promotion_expiry_days),
                created_at=now, updated_at=now)
            await uow.models.add_promotion(promotion)
            await self._emit(uow, ctx, "model_version.promotion_requested",
                             promotion_urn(ctx.tenant_id, promotion.id),
                             {"promotion_id": promotion.id, "model_id": model_id,
                              "version": version, "target_stage": target_stage,
                              "requested_by": ctx.actor_id, "via_agent": ctx.via_agent})
            await uow.commit()
        return {"operation_id": promotion.workflow_id, "promotion_id": promotion.id,
                "status": "pending"}

    async def decide(self, ctx: CallCtx, promotion_id: str, decision: str,
                     message: str | None = None, target_stage: str | None = None) -> dict:
        async with self.uow(ctx.tenant_id) as uow:
            promotion = await uow.models.get_promotion(promotion_id)
            if not promotion:
                raise NotFound("promotion not found")
            if promotion.status != PROMOTION_STATUS["pending"]:
                raise Conflict("promotion already decided")
            # BR-6 four-eyes: requester (or the OBO user of a requesting agent)
            # cannot approve their own promotion.
            if ctx.actor_id == promotion.requested_by:
                raise SelfApprovalForbidden("approver must differ from requester (BR-6)")

            v = await uow.models.get_version_by_id(promotion.model_version_id)
            model = await uow.models.get_model(v.model_id)
            now = self.clock.now()
            trace_id = ctx.trace_id or str(uuid7())

            if decision == "edit":
                if target_stage not in STAGE:
                    raise ValidationFailed("edit requires a valid target_stage")
                validate_stage_transition(v.stage, STAGE[target_stage])
                promotion.target_stage = STAGE[target_stage]
                promotion.updated_at = now
                await uow.models.update_promotion(promotion)
                await uow.commit()
                return {"promotion_id": promotion.id, "status": "pending",
                        "target_stage": target_stage}

            if decision == "reject":
                promotion.status = PROMOTION_STATUS["rejected"]
                promotion.decision = {"actor": ctx.actor_id, "decided_at": now.isoformat(),
                                      "message": message}
                promotion.decided_at = now
                promotion.updated_at = now
                await uow.models.update_promotion(promotion)
                await self._emit(uow, ctx, "model_version.promotion_rejected",
                                 promotion_urn(ctx.tenant_id, promotion.id),
                                 {"promotion_id": promotion.id, "model_id": v.model_id,
                                  "version": v.version, "reason": message},
                                 trace_id=trace_id)
                await uow.commit()
                return {"promotion_id": promotion.id, "status": "rejected",
                        "decision": {"actor": ctx.actor_id, "decided_at": now.isoformat(),
                                     "message": message}}

            if decision != "approve":
                raise ValidationFailed("decision must be approve | reject | edit")

            # APPROVE: take the per-model mutex (BR-4) FIRST so concurrent
            # production approvals serialize — the second waits here, then
            # re-reads the incumbent below instead of racing the single-
            # production unique index into a 500.
            await uow.models.lock_model(v.model_id)
            # re-read the version now that we hold the lock (its stage may have
            # been changed by a concurrent approval that just committed).
            v = await uow.models.get_version_by_id(promotion.model_version_id)
            validate_stage_transition(v.stage, promotion.target_stage)
            from_stage = v.stage
            # single-production invariant (BR-5/AC-7): auto-archive the incumbent
            if promotion.target_stage == STAGE["production"]:
                incumbent = await uow.models.production_version(v.model_id)
                if incumbent and incumbent.id != v.id:
                    incumbent.stage = STAGE["archived"]
                    incumbent.stage_updated_at = now
                    incumbent.updated_at = now
                    await uow.models.update_version(incumbent)
                    await self._emit(uow, ctx, "model_version.archived",
                                     model_version_urn(ctx.tenant_id, v.model_id,
                                                       incumbent.version),
                                     {"model_id": v.model_id, "version": incumbent.version,
                                      "cause": "superseded"}, trace_id=trace_id)
            v.stage = promotion.target_stage
            v.stage_updated_at = now
            v.updated_at = now
            try:
                await uow.models.update_version(v)
            except IntegrityError as exc:  # defensive: single-production index
                raise Conflict(
                    "a concurrent production promotion won; re-request") from exc

            promotion.status = PROMOTION_STATUS["approved"]
            promotion.decision = {"actor": ctx.actor_id, "decided_at": now.isoformat(),
                                  "message": message}
            promotion.decided_at = now
            promotion.updated_at = now
            await uow.models.update_promotion(promotion)

            await self._refresh_card(uow, ctx, v, model)
            await self._emit(uow, ctx, "model_version.promoted",
                             model_version_urn(ctx.tenant_id, v.model_id, v.version),
                             {"model_id": v.model_id, "version": v.version,
                              "from_stage": STAGE_LABELS[from_stage],
                              "to_stage": STAGE_LABELS[v.stage],
                              "promotion_id": promotion.id, "decision_actor": ctx.actor_id},
                             trace_id=trace_id)
            # capture what the MLflow registry must reflect (applied post-commit)
            source_run = await uow.runs.get(v.source_run_id)
            mlflow_sync = {
                "model_name": model.name, "version_id": v.id, "target_stage": v.stage,
                "existing_ref": v.mlflow_model_ref,
                "run_mlflow_id": source_run.mlflow_run_id if source_run else None,
                "artifact_uri": source_run.artifact_uri if source_run else None,
            }
            await uow.commit()
        # BUG-2: reflect the approved stage in the MLflow model registry so
        # inference-service (which resolves models:/<name>/<stage>) sees it. Part
        # of the governed approval (awaited before returning); best-effort so an
        # MLflow blip never rolls back the authoritative Postgres decision.
        await self._sync_mlflow_stage(ctx.tenant_id, mlflow_sync)
        return {"promotion_id": promotion.id, "status": "approved",
                "decision": {"actor": ctx.actor_id, "decided_at": now.isoformat()}}

    async def _sync_mlflow_stage(self, tenant_id: str, sync: dict) -> None:
        name = sync["model_name"]
        mlflow_stage = STAGE_TO_MLFLOW[sync["target_stage"]]
        try:
            await self.deps.mlflow.ensure_registered_model(name)
            ref = sync.get("existing_ref") or ""
            if ref.startswith("models:/"):
                mlflow_version = ref.rsplit("/", 1)[1]
                new_ref = None
            else:
                source = sync.get("artifact_uri") or f"runs:/{sync.get('run_mlflow_id')}/model"
                mlflow_version = await self.deps.mlflow.create_model_version(
                    name, source, sync.get("run_mlflow_id"))
                new_ref = f"models:/{name}/{mlflow_version}"
            await self.deps.mlflow.transition_model_version_stage(
                name, mlflow_version, mlflow_stage,
                archive_existing=(sync["target_stage"] == STAGE["production"]))
        except Exception:  # noqa: BLE001 — mirror stays authoritative on MLflow failure
            logger.exception("MLflow stage sync failed for model %s", name)
            return
        if new_ref:
            async with self.uow(tenant_id) as uow:
                vv = await uow.models.get_version_by_id(sync["version_id"])
                if vv and not (vv.mlflow_model_ref or "").startswith("models:/"):
                    vv.mlflow_model_ref = new_ref
                    vv.updated_at = self.clock.now()
                    await uow.models.update_version(vv)
                    await uow.commit()

    async def _refresh_card(self, uow, ctx, version: ModelVersion, model) -> None:
        card = await uow.models.get_card(version.id)
        if card is None:
            return
        promos = (await uow.models.list_promotions(version.id, 50, None)).items
        history = [{"from_stage": STAGE_LABELS[p.from_stage],
                    "to_stage": STAGE_LABELS[p.target_stage],
                    "status": _promotion_status_label(p.status),
                    "decision_actor": (p.decision or {}).get("actor")} for p in promos]
        card.auto_fields["stage"] = STAGE_LABELS[version.stage]
        card.auto_fields["stage_updated_at"] = (
            version.stage_updated_at.isoformat() if version.stage_updated_at else None)
        card.auto_fields["promotion_history"] = history
        card.updated_at = self.clock.now()
        await uow.models.upsert_card(card)

    async def list_promotions(self, ctx: CallCtx, model_id: str, version: int,
                              limit: int, cursor: str | None):
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.models.get_model(model_id):
                raise NotFound("model not found")
            v = await uow.models.get_version(model_id, version)
            if not v:
                raise NotFound("model version not found")
            page = await uow.models.list_promotions(v.id, limit, cursor)
        page.items = [_promotion_payload(ctx, p) for p in page.items]
        return page

    async def expire_pending_for_tenant(self, tenant_id: str) -> int:
        """Per-tenant expiry sweep (EXP-FR-033/BR-7): pending promotions past the
        14-day timeout -> expired. Durable state re-derives the cutoff from
        ``expires_at`` so this is restart-safe. The worker loop enumerates the
        tenants with pending promotions and calls this under normal (RLS-bound)
        tenant sessions."""
        now = self.clock.now()
        ctx = CallCtx(tenant_id=tenant_id,
                      actor={"type": "service", "id": "promotion-expiry"})
        count = 0
        async with self.uow(tenant_id) as uow:
            expired = await uow.models.pending_expired_before(now)
            for promotion in expired:
                if promotion.status != PROMOTION_STATUS["pending"]:
                    continue
                v = await uow.models.get_version_by_id(promotion.model_version_id)
                promotion.status = PROMOTION_STATUS["expired"]
                promotion.decided_at = now
                promotion.updated_at = now
                await uow.models.update_promotion(promotion)
                await self._emit(uow, ctx, "model_version.promotion_expired",
                                 promotion_urn(tenant_id, promotion.id),
                                 {"promotion_id": promotion.id, "model_id": v.model_id,
                                  "version": v.version, "reason": "timeout"})
                count += 1
            await uow.commit()
        return count


def _promotion_status_label(code: int) -> str:
    from app.domain.entities import PROMOTION_STATUS_LABELS

    return PROMOTION_STATUS_LABELS[code]


def _promotion_payload(ctx: CallCtx, p: Promotion) -> dict:
    return {"id": p.id, "urn": promotion_urn(ctx.tenant_id, p.id),
            "model_version_id": p.model_version_id,
            "target_stage": STAGE_LABELS[p.target_stage],
            "from_stage": STAGE_LABELS[p.from_stage],
            "status": _promotion_status_label(p.status), "rationale": p.rationale,
            "requested_by": p.requested_by, "via_agent": p.via_agent, "decision": p.decision,
            "created_at": p.created_at.isoformat() if p.created_at else None}


class CardService(_Base):
    async def get_card(self, ctx: CallCtx, model_id: str, version: int,
                       fmt: str | None = None):
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.models.get_model(model_id):
                raise NotFound("model not found")
            v = await uow.models.get_version(model_id, version)
            if not v:
                raise NotFound("model version not found")
            card = await uow.models.get_card(v.id)
            if not card:
                raise NotFound("model card not found")
        if fmt == "markdown":
            return card_mod.render_markdown(card.auto_fields, card.overlay)
        return card_mod.merge_card(card.auto_fields, card.overlay)

    async def patch_overlay(self, ctx: CallCtx, model_id: str, version: int,
                            changes: dict) -> dict:
        async with self.uow(ctx.tenant_id) as uow:
            if not await uow.models.get_model(model_id):
                raise NotFound("model not found")
            v = await uow.models.get_version(model_id, version)
            if not v:
                raise NotFound("model version not found")
            card = await uow.models.get_card(v.id)
            if not card:
                raise NotFound("model card not found")
            for key in card_mod.OVERLAY_FIELDS:
                if key in changes and changes[key] is not None:
                    card.overlay[key] = changes[key]
            card.overlay_version += 1
            card.overlay_updated_by = ctx.actor_id
            card.updated_at = self.clock.now()
            await uow.models.upsert_card(card)
            await self._emit(uow, ctx, "model_card.updated",
                             model_version_urn(ctx.tenant_id, model_id, version),
                             {"model_version_id": v.id, "overlay_version": card.overlay_version})
            await uow.commit()
        return card_mod.merge_card(card.auto_fields, card.overlay)

    async def flag_dataset_deleted(self, tenant_id: str, dataset_urn: str) -> int:
        """EXP-FR-040/§6: on dataset.deleted, flag every model card whose training
        input references the dataset (training_data_unavailable=true). Idempotent."""
        ctx = CallCtx(tenant_id=tenant_id,
                      actor={"type": "service", "id": "dataset-events"})
        flagged = 0
        async with self.uow(tenant_id) as uow:
            cards = await uow.models.cards_referencing_dataset(dataset_urn)
            for card in cards:
                if card.auto_fields.get("training_data_unavailable"):
                    continue
                card.auto_fields["training_data_unavailable"] = True
                card.updated_at = self.clock.now()
                await uow.models.upsert_card(card)
                await self._emit(
                    uow, ctx, "model_card.updated",
                    f"wr:{tenant_id}:experiment:model_version/{card.model_version_id}",
                    {"model_version_id": card.model_version_id,
                     "training_data_unavailable": True, "dataset_urn": dataset_urn})
                flagged += 1
            await uow.commit()
        return flagged
