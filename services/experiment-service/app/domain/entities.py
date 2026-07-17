"""Domain entities & enums (BRD §4.1/§4.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# --- enums (stored as SMALLINT; string labels for API) ----------------------

MODEL_TYPES = {
    "anomaly_detection": 0,
    "classification": 1,
    "regression": 2,
    "forecasting": 3,
    "unsupervised": 4,
    "clustering": 5,
}
MODEL_TYPE_LABELS = {v: k for k, v in MODEL_TYPES.items()}

# Run status (MLflow-aligned, V1 mapping kept) — EXP-FR-004.
RUN_STATUS = {"scheduled": 0, "running": 1, "finished": 2, "failed": 3, "killed": 4}
RUN_STATUS_LABELS = {v: k for k, v in RUN_STATUS.items()}
RUN_UI_LABELS = {
    0: "Pending", 1: "Processing", 2: "Ready", 3: "Failed", 4: "Failed",
}

# Model-version stage — EXP-FR-032.
STAGE = {"none": 0, "staging": 1, "production": 2, "archived": 3}
STAGE_LABELS = {v: k for k, v in STAGE.items()}

# Promotion status.
PROMOTION_STATUS = {
    "pending": 0, "approved": 1, "rejected": 2, "expired": 3, "cancelled": 4,
}
PROMOTION_STATUS_LABELS = {v: k for k, v in PROMOTION_STATUS.items()}


def model_type_code(name: str) -> int:
    if name not in MODEL_TYPES:
        from app.domain.errors import ValidationFailed

        raise ValidationFailed(f"unknown model_type {name!r}")
    return MODEL_TYPES[name]


# --- dataclasses ------------------------------------------------------------


@dataclass(slots=True)
class Experiment:
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    model_type: int
    mlflow_experiment_id: str
    model_pipeline_urn: str
    feature_engineering_pipeline_urn: str
    training_pipeline_urn: str
    description: str | None = None
    note: str | None = None
    tags: dict = field(default_factory=dict)
    created_by: str = "unknown"
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
    deleted_at: datetime | None = None


@dataclass(slots=True)
class Run:
    id: str
    tenant_id: str
    experiment_id: str
    mlflow_run_id: str
    status: int
    name: str | None = None
    algorithm: str = ""
    artifact_uri: str | None = None
    pipeline_run_urn: str | None = None
    input_dataset_urns: list[str] = field(default_factory=list)
    output_dataset_urns: list[str] = field(default_factory=list)
    error_messages: dict | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_by: str = "unknown"
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
    deleted_at: datetime | None = None


@dataclass(slots=True)
class RunParam:
    run_id: str
    tenant_id: str
    key: str
    value: str
    is_hidden: bool = False
    param_conflict: bool = False


@dataclass(slots=True)
class RunMetric:
    run_id: str
    tenant_id: str
    key: str
    value: float
    step: int
    logged_at: datetime


@dataclass(slots=True)
class RunTag:
    run_id: str
    tenant_id: str
    key: str
    value: str


@dataclass(slots=True)
class RunArtifact:
    run_id: str
    tenant_id: str
    path: str
    size_bytes: int
    content_type: str | None = None


@dataclass(slots=True)
class RegisteredModel:
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    model_type: int
    owner_id: str
    description: str | None = None
    created_by: str = "unknown"
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
    deleted_at: datetime | None = None


@dataclass(slots=True)
class ModelVersion:
    id: str
    tenant_id: str
    model_id: str
    version: int
    source_run_id: str
    stage: int
    mlflow_model_ref: str | None = None
    flavor: str = "mlflow.sklearn"
    input_schema: dict | None = None
    output_schema: dict | None = None
    stage_updated_at: datetime | None = None
    created_by: str = "unknown"
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
    deleted_at: datetime | None = None


@dataclass(slots=True)
class Promotion:
    id: str
    tenant_id: str
    model_version_id: str
    target_stage: int
    from_stage: int
    status: int
    rationale: str | None = None
    requested_by: str = "unknown"
    via_agent: dict | None = None
    workflow_id: str | None = None
    decision: dict | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]


@dataclass(slots=True)
class ModelCard:
    model_version_id: str
    tenant_id: str
    auto_fields: dict
    overlay: dict = field(default_factory=dict)
    overlay_updated_by: str | None = None
    overlay_version: int = 0
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
