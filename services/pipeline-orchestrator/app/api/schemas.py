"""Request models + response serializers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import ModelType, PipelineType, RunStatus

_PT = Literal["data_prep", "feature_engineering", "model", "training", "inference",
              "profiling", "scheduled"]
_MT = Literal["anomaly_detection", "classification", "regression", "forecasting",
              "unsupervised", "clustering"]


class TemplateCreate(BaseModel):
    workspace_id: str
    name: str = Field(min_length=1, max_length=255)
    pipeline_type: _PT
    model_type: _MT | None = None
    algorithm_template_name: str | None = None
    definition: dict = Field(default_factory=lambda: {"nodes": [], "edges": []})
    run_parameters: dict = Field(default_factory=dict)
    is_system: bool = False


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    definition: dict | None = None
    run_parameters: dict | None = None


class ValidateRequest(BaseModel):
    pipeline_type: _PT
    model_type: _MT | None = None
    definition: dict


class RunRequest(BaseModel):
    run_parameters: dict = Field(default_factory=dict)


class InstantiateRequest(BaseModel):
    workspace_id: str
    name: str | None = None
    mode: Literal["train", "tune", "cross_validation"] = "train"
    dataset_refs: dict[str, str] = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)


class ScheduleCreate(BaseModel):
    template_id: str
    cron: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=255)
    timezone: str = "UTC"
    run_parameters: dict = Field(default_factory=dict)


class QuotaUpdate(BaseModel):
    max_concurrent_runs: int | None = None
    max_concurrent_pods: int | None = None
    max_run_duration_minutes: int | None = None
    min_seconds_between_runs: int | None = None
    node_pool: str | None = None
    resource_ceiling: dict | None = None


def template_payload(t, version=None) -> dict:
    data = {
        "id": t.id, "workspace_id": t.workspace_id, "name": t.name,
        "pipeline_type": PipelineType(t.pipeline_type).name,
        "model_type": ModelType(t.model_type).name if t.model_type is not None else None,
        "algorithm_template_name": t.algorithm_template_name,
        "active_version_id": t.active_version_id, "is_system": t.is_system,
        "archived": t.deleted_at is not None,
        "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat(),
    }
    if version is not None:
        data["validation_status"] = "valid" if version.validation_status == 1 else "draft"
        data["validation_report"] = version.validation_report
        data["manifest_digest"] = version.manifest_digest
        # The active version's DAG definition, so a client (e.g. the no-code builder's
        # edit mode) can rehydrate the canvas from the saved template. Only present on
        # single-template reads (get/create/update/activate/clone), never on the list.
        data["definition"] = version.definition
    return data


def version_payload(v) -> dict:
    return {
        "id": v.id, "template_id": v.template_id, "version_no": v.version_no,
        "validation_status": "valid" if v.validation_status == 1 else "draft",
        "validation_report": v.validation_report,
        "run_parameters": v.run_parameters, "global_parameters": v.global_parameters,
        "component_catalog_version": v.component_catalog_version,
        "manifest_digest": v.manifest_digest,
        "argo_template_name": v.argo_template_name,
        "created_at": v.created_at.isoformat(),
    }


def run_payload(r) -> dict:
    return {
        "id": r.id, "template_id": r.template_id, "version_id": r.version_id,
        "status": RunStatus(r.status).name,
        "argo_workflow_name": r.argo_workflow_name, "mlflow_run_id": r.mlflow_run_id,
        "run_parameters": r.run_parameters, "components_status": r.components_status,
        "error": r.error, "input_dataset_urns": r.input_dataset_urns,
        "output_dataset_urns": r.output_dataset_urns,
        "retried_from_run_id": r.retried_from_run_id, "submitted_by": r.submitted_by,
        "via_agent": r.via_agent, "model_uri": r.model_uri, "metrics": r.metrics,
        "created_at": r.created_at.isoformat(),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def schedule_payload(s) -> dict:
    return {
        "id": s.schedule_id, "template_id": s.template_id, "name": s.name,
        "cron": s.cron, "timezone": s.timezone, "run_parameters": s.run_parameters,
        "enabled": s.enabled, "last_run_id": s.last_run_id,
        "next_fire_at": s.next_fire_at.isoformat() if s.next_fire_at else None,
        "last_fire_at": s.last_fire_at.isoformat() if s.last_fire_at else None,
        "created_by": s.created_by,
        "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat(),
    }


def component_payload(c) -> dict:
    types = {0: "io", 1: "data_prep", 2: "algorithm", 3: "utility", 4: "comment"}
    return {
        "name": c.name, "component_type": types.get(c.component_type, "other"),
        "label": c.label, "enabled": c.enabled,
        "catalog_version": c.catalog_version, "image_digest": c.image_digest,
        "min_inputs": c.definition.get("min_inputs"),
        "max_inputs": c.definition.get("max_inputs"),
        "max_outputs": c.definition.get("max_outputs"),
        "outputs": c.definition.get("outputs"),
        "parameters": c.definition.get("parameters"),
    }


def algorithm_payload(a) -> dict:
    return {
        "name": a.name, "label": a.label,
        "model_type": ModelType(a.model_type).name, "order": a.order,
        "input_type": a.input_type, "parameters": a.parameters,
        "tuning_parameters": a.tuning_parameters, "runnable": a.runnable,
        "metadata": a.metadata,
    }


def page_envelope(items, next_cursor, has_more) -> dict:
    return {"data": items, "page": {"next_cursor": next_cursor, "has_more": has_more}}
