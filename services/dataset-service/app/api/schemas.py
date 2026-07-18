"""Request models + response serializers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.entities import Dataset, DatasetVersion
from app.domain.naming import RESOLVE_NAMESPACE
from app.domain.naming import safe_relation as _safe_relation
from app.domain.urn import dataset_urn, version_urn
from app.utils import etag_for


class DatasetCreate(BaseModel):
    workspace_id: str
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    visibility: Literal["workspace", "tenant_public"] = "workspace"
    iceberg_table: str | None = None
    partition_spec: dict | None = None
    tags: list[str] = Field(default_factory=list)
    custom_metadata: dict[str, str] | None = None


class DatasetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    visibility: Literal["workspace", "tenant_public"] | None = None
    lifecycle: Literal["active", "deprecated"] | None = None
    successor_urn: str | None = None
    tags: list[str] | None = None
    partition_spec: dict | None = None
    custom_metadata: dict[str, str] | None = None


class VersionRegister(BaseModel):
    tenant_id: str
    iceberg_snapshot_id: int
    schema_: dict = Field(alias="schema", default_factory=dict)
    row_count: int | None = None
    bytes: int | None = None
    produced_by_urn: str | None = None
    skip_profiling: bool = False


class ProfileResult(BaseModel):
    tenant_id: str
    status: Literal["completed", "failed"]
    error_category: str | None = None
    error_message: str | None = None
    object_key_json: str | None = None
    object_key_html: str | None = None
    summary: dict | None = None
    sample: dict | None = None
    profiler_version: str | None = None


class EdgeCreate(BaseModel):
    from_urn: str
    to_urn: str
    activity: str
    run_urn: str | None = None
    properties: dict | None = None
    occurred_at: datetime | None = None


class SimilarRequest(BaseModel):
    schema_: dict | None = Field(alias="schema", default=None)
    columns: list[str] | None = None


# ---------------------------------------------------------------------------
# Serializers


def version_payload(v: DatasetVersion) -> dict:
    return {
        "id": v.id,
        "urn": version_urn(v.tenant_id, v.dataset_id, v.version_no),
        "dataset_id": v.dataset_id,
        "version_no": v.version_no,
        "iceberg_snapshot_id": v.iceberg_snapshot_id,
        "schema": v.schema,
        "schema_diff": v.schema_diff,
        "breaking_change": v.breaking_change,
        "row_count": v.row_count,
        "bytes": v.bytes,
        "produced_by_urn": v.produced_by_urn,
        "profile_status": str(v.profile_status),
        "expired": v.expired,
        "created_at": v.created_at.isoformat(),
    }


def dataset_payload(ds: Dataset, current: DatasetVersion | None = None) -> dict:
    payload = {
        "id": ds.id,
        "urn": dataset_urn(ds.tenant_id, ds.id),
        "workspace_id": ds.workspace_id,
        "name": ds.name,
        "description": ds.description,
        "status": str(ds.status),
        "lifecycle": str(ds.lifecycle),
        "successor_urn": ds.successor_urn,
        "visibility": str(ds.visibility),
        "iceberg_table": ds.iceberg_table,
        "partition_spec": ds.partition_spec,
        "tags": ds.tags,
        "custom_metadata": ds.custom_metadata,
        "error_log": ds.error_log,
        "created_by": ds.created_by,
        "created_at": ds.created_at.isoformat(),
        "updated_at": ds.updated_at.isoformat(),
        "deleted_at": ds.deleted_at.isoformat() if ds.deleted_at else None,
        "etag": etag_for(ds.updated_at),
    }
    if ds.lifecycle == "deprecated":
        payload["warnings"] = [
            {"code": "DATASET_DEPRECATED", "successor_urn": ds.successor_urn}
        ]
    if current is not None:
        payload["current_version"] = {
            "version_no": current.version_no,
            "iceberg_snapshot_id": current.iceberg_snapshot_id,
            "row_count": current.row_count,
            "bytes": current.bytes,
            "breaking_change": current.breaking_change,
            "profile_status": str(current.profile_status),
        }
    else:
        payload["current_version"] = None
    return payload


def page_envelope(items: list[dict], next_cursor: str | None, has_more: bool) -> dict:
    return {"data": items, "page": {"next_cursor": next_cursor, "has_more": has_more}}


def _quote_ident(*parts: str) -> str:
    """Engine-quoted dotted identifier (double-quote each part; BR-1)."""
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def resolve_payload(
    dataset: Dataset,
    version: DatasetVersion,
    source_uris: list[str],
    columns: list[dict],
) -> dict:
    """Physical resolution for query-service. Matches the Go `Meta` json fields
    (name/version/urn/physical_ident/namespace/size_bytes/row_count/columns/
    deprecated) byte-for-byte, plus the physical source (source_uris/
    source_format) query-service reads to run SQL over the parquet directly."""
    relation = _safe_relation(dataset.name)
    return {
        "name": dataset.name,
        "version": version.version_no,
        "urn": dataset_urn(dataset.tenant_id, dataset.id),
        # namespace "main" + physical_ident "main"."<relation>" (each part
        # double-quoted). query-service CREATE OR REPLACE TABLE main.<relation>.
        "namespace": RESOLVE_NAMESPACE,
        "physical_ident": _quote_ident(RESOLVE_NAMESPACE, relation),
        "row_count": version.row_count,
        "size_bytes": version.bytes,
        "columns": columns,
        "source_uris": source_uris,
        "source_format": "parquet",
        "deprecated": str(dataset.lifecycle) == "deprecated",
    }
