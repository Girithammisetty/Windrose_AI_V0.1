"""Request models (BRD 03 §5)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_WORKSPACE = "00000000-0000-0000-0000-000000000000"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionCreate(_Body):
    name: str = Field(min_length=1, max_length=255)
    connector_type: str
    config: dict[str, Any]
    secrets: dict[str, Any] = Field(default_factory=dict)
    traffic_direction: Literal["incoming", "outgoing", "both"] = "incoming"
    tags: list[str] = Field(default_factory=list)
    workspace_id: str = DEFAULT_WORKSPACE
    skip_test: bool = False


class ConnectionUpdate(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    secrets: dict[str, Any] | None = None
    traffic_direction: Literal["incoming", "outgoing", "both"] | None = None
    tags: list[str] | None = None
    skip_test: bool = False


class WritebackCreate(_Body):
    """Enqueue a decision write-back to a tenant's system of record (INS-FR-061).
    `connection_id` must be an `outgoing` connection. `target` carries executor
    routing (db_upsert: {schema, table, key_column}; http_post: {path?, method?});
    `payload` is the decision snapshot (db_upsert: the row columns incl. the key;
    http_post: the JSON body)."""

    connection_id: str
    decision_kind: str = Field(min_length=1, max_length=128)
    decision_ref: str = Field(min_length=1, max_length=512)
    idempotency_key: str = Field(min_length=1, max_length=256)
    target: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    approval_mode: Literal["four_eyes", "auto"] = "four_eyes"
    workspace_id: str = DEFAULT_WORKSPACE


class ConnectionTestAdhoc(_Body):
    connector_type: str
    config: dict[str, Any]
    secrets: dict[str, Any] = Field(default_factory=dict)


class PreviewRequest(_Body):
    table: str | None = None
    path: str | None = None
    query: str | None = None
    limit: int = Field(default=100, ge=1, le=100)


class NewDataset(_Body):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class IngestionCreate(_Body):
    ingestion_mode: Literal["file_upload", "query", "scheduled_run", "webhook_batch"]
    connection_id: str | None = None
    statement: str | None = None
    file_format: str | None = None
    dataset_urn: str | None = None
    new_dataset: NewDataset | None = None
    skip_profiling: bool = False
    allow_empty: bool = False
    error_row_limit: int = Field(default=100, ge=0, le=10_000)
    workspace_id: str = DEFAULT_WORKSPACE


class UploadCreate(_Body):
    ingestion_id: str
    part_size: int | None = None
    bytes_total: int | None = None


class PartManifestEntry(_Body):
    n: int = Field(ge=1)
    etag: str
    size: int = Field(ge=0)


class UploadComplete(_Body):
    parts: list[PartManifestEntry] = Field(min_length=1)
    sha256: str | None = None


class WatermarkCreate(_Body):
    column: str
    operator: str = ">"
    value_type: Literal["int", "decimal", "timestamp", "date", "string"] = "string"
    initial_value: str


class ScheduleCreate(_Body):
    connection_id: str
    ingestion_template: dict[str, Any]
    cron: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    timezone: str = "UTC"
    watermark: WatermarkCreate | None = None
    overlap_policy: Literal["skip", "buffer_one"] = "skip"
    enabled: bool = True
    workspace_id: str = DEFAULT_WORKSPACE


class ScheduleUpdate(_Body):
    cron: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    timezone: str | None = None
    ingestion_template: dict[str, Any] | None = None
    overlap_policy: Literal["skip", "buffer_one"] | None = None
    enabled: bool | None = None
