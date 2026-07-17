"""Pydantic request/response models + payload builders (BRD §5)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.entities import InferenceJob, ScoringSchedule
from app.domain.enums import output_mode_from_str, stage_name, status_name


class OutputSpec(BaseModel):
    dataset_name: str | None = None
    mode: str | None = None  # create | append | replace


class SubmitBody(BaseModel):
    model_version_urn: str
    input_dataset_urn: str
    name: str | None = None
    description: str | None = None
    parameters: dict[str, Any] | None = None
    output: OutputSpec | None = None
    allow_unpromoted: bool = False
    allow_empty: bool = False


class ValidateBody(BaseModel):
    model_version_urn: str
    input_dataset_urn: str
    allow_unpromoted: bool = False
    allow_empty: bool = False


class BulkBody(BaseModel):
    model_version_urn: str
    input_dataset_urns: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] | None = None
    output: OutputSpec | None = None


class ScheduleBody(BaseModel):
    name: str
    input_selector: dict[str, Any]
    output: dict[str, Any]
    model_version_urn: str | None = None
    model_urn: str | None = None
    stage_selector: str | None = None
    cron: str | None = None
    interval_seconds: int | None = None
    timezone: str = "UTC"
    overlap_policy: str | None = None
    enabled: bool = True
    notify_on_failure: bool = True


class SchedulePatch(BaseModel):
    cron: str | None = None
    interval_seconds: int | None = None
    timezone: str | None = None
    overlap_policy: str | None = None
    input_selector: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    notify_on_failure: bool | None = None


def output_mode_value(spec: OutputSpec | None) -> int:
    if spec is None:
        return 0
    return int(output_mode_from_str(spec.mode))


def page_envelope(items: list, next_cursor: str | None, has_more: bool) -> dict:
    return {"data": items, "page": {"next_cursor": next_cursor, "has_more": has_more}}


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def job_payload(job: InferenceJob) -> dict:
    model = {
        "urn": job.model_version_urn,
        "name": job.model_name,
        "version": job.model_version,
        "stage_at_submit": stage_name(job.model_stage_at_submit),
    }
    return {
        "id": job.id,
        "status": status_name(job.status),
        "name": job.name,
        "description": job.description,
        "model": model,
        "input_dataset": {"urn": job.input_dataset_urn, "version": job.input_dataset_version},
        "output_dataset": (
            {"urn": job.output_dataset_urn, "version": job.output_dataset_version}
            if job.output_dataset_urn else None
        ),
        "output_mode": job.output_mode,
        "parameters": job.parameters,
        "compatibility_report": job.compatibility_report,
        "pipeline_run_urn": job.pipeline_run_urn,
        "components_status": job.components_status,
        "error": job.error,
        "row_count": job.row_count,
        "schedule_id": job.schedule_id,
        "retried_from_job_id": job.retried_from_job_id,
        "via_agent": job.via_agent,
        "timestamps": {
            "queued_at": _iso(job.queued_at),
            "submitted_at": _iso(job.submitted_at),
            "started_at": _iso(job.started_at),
            "finished_at": _iso(job.finished_at),
            "created_at": _iso(job.created_at),
        },
    }


def schedule_payload(sch: ScoringSchedule) -> dict:
    return {
        "id": sch.id,
        "name": sch.name,
        "enabled": sch.enabled,
        "paused_reason": sch.paused_reason,
        "model_version_urn": sch.model_version_urn,
        "model_urn": sch.model_urn,
        "stage_selector": stage_name(sch.stage_selector),
        "input_selector": sch.input_selector,
        "output": sch.output,
        "cron": sch.cron,
        "interval_seconds": sch.interval_seconds,
        "timezone": sch.timezone,
        "overlap_policy": sch.overlap_policy,
        "consecutive_failures": sch.consecutive_failures,
        "temporal_schedule_id": sch.temporal_schedule_id,
        "notify_on_failure": sch.notify_on_failure,
        "next_fire_preview": {"at": _iso(sch.next_fire_at)},
    }
