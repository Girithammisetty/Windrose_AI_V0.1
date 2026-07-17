"""Enums for inference jobs and schedules (BRD §4.2)."""

from __future__ import annotations

from enum import IntEnum


class JobStatus(IntEnum):
    validating = 0
    rejected = 1
    queued = 2
    submitted = 3
    running = 4
    finalizing = 5
    succeeded = 6
    failed = 7
    cancelling = 8
    cancelled = 9


class OutputMode(IntEnum):
    create = 0
    append = 1
    replace = 2


class OverlapPolicy(IntEnum):
    skip = 0
    queue = 1
    cancel_running = 2


class ModelStage(IntEnum):
    none = 0
    staging = 1
    production = 2
    archived = 3


TERMINAL = {JobStatus.rejected, JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}
TERMINAL_FAILURE = {JobStatus.rejected, JobStatus.failed, JobStatus.cancelled}
NON_TERMINAL = {
    JobStatus.validating,
    JobStatus.queued,
    JobStatus.submitted,
    JobStatus.running,
    JobStatus.finalizing,
    JobStatus.cancelling,
}
# cancel allowed from queued/submitted/running (validating + finalizing non-cancellable, §4.3)
CANCELLABLE = {JobStatus.queued, JobStatus.submitted, JobStatus.running}


_STATUS_NAMES = {s: s.name for s in JobStatus}
_STAGE_NAMES = {s: s.name for s in ModelStage}
_OUTPUT_MODE_NAMES = {m: m.name for m in OutputMode}
_OVERLAP_NAMES = {p: p.name for p in OverlapPolicy}


def status_name(status: int) -> str:
    return _STATUS_NAMES.get(JobStatus(status), str(status))


def stage_name(stage: int | None) -> str | None:
    if stage is None:
        return None
    return _STAGE_NAMES.get(ModelStage(stage))


def stage_from_mlflow(current_stage: str | None) -> ModelStage:
    """Map MLflow ``current_stage`` (Production/Staging/Archived/None) to our enum."""
    mapping = {
        "production": ModelStage.production,
        "staging": ModelStage.staging,
        "archived": ModelStage.archived,
        "none": ModelStage.none,
    }
    return mapping.get((current_stage or "none").strip().lower(), ModelStage.none)


def output_mode_from_str(value: str | None) -> OutputMode:
    if value is None:
        return OutputMode.create
    try:
        return OutputMode[value]
    except KeyError as exc:
        raise ValueError(f"invalid output mode {value!r}") from exc


def overlap_from_str(value: str | None) -> OverlapPolicy:
    if value is None:
        return OverlapPolicy.skip
    try:
        return OverlapPolicy[value]
    except KeyError as exc:
        raise ValueError(f"invalid overlap_policy {value!r}") from exc
