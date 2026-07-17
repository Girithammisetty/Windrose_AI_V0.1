"""Enums per BRD §4.2 — stored as SMALLINT, exchanged as strings on the API."""

from __future__ import annotations

from enum import IntEnum


class PipelineType(IntEnum):
    data_prep = 0
    feature_engineering = 1
    model = 2
    training = 3
    inference = 4
    profiling = 5
    scheduled = 6


class ModelType(IntEnum):
    anomaly_detection = 0
    classification = 1
    regression = 2
    forecasting = 3
    unsupervised = 4
    clustering = 5


class RunStatus(IntEnum):
    pending = 0
    quota_queued = 1
    submitted = 2
    running = 3
    succeeded = 4
    failed = 5
    cancelled = 6


TERMINAL_STATUSES = {RunStatus.succeeded, RunStatus.failed, RunStatus.cancelled}
TERMINATABLE_STATUSES = {
    RunStatus.pending,
    RunStatus.quota_queued,
    RunStatus.submitted,
    RunStatus.running,
}

# Pipeline types that are composable building blocks, not directly runnable
# (PIPE-FR-013 / AC-14).
NON_RUNNABLE_TYPES = {PipelineType.model, PipelineType.feature_engineering}

# Typed port kinds carried on DAG edges (PIPE-FR-011).
PORT_TYPES = {"dataframe", "model", "metrics", "json", "dataset_ref"}


def pipeline_type_from_str(value: str) -> PipelineType:
    try:
        return PipelineType[value]
    except KeyError as exc:
        raise ValueError(f"unknown pipeline_type {value!r}") from exc


def model_type_from_str(value: str | None) -> ModelType | None:
    if value is None:
        return None
    try:
        return ModelType[value]
    except KeyError as exc:
        raise ValueError(f"unknown model_type {value!r}") from exc
