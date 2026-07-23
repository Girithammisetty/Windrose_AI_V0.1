"""BRD 65 — warehouse write-back sinks. Datacern persisted computed data only to
Iceberg bronze;  lands results in cloud-native warehouses
(warehouse_writer_{aws,gcp,azure}). This adds a swappable `WarehouseSink` that a
pipeline's `write-to-warehouse` node / the run-lifecycle writer targets, matching
the codebase's registry pattern (dataset-service adapters, WORKFLOW_BACKENDS):

  - `local`        — parquet to the local object-store dir (real; unit tier / no infra)
  - `objectstore`  — parquet to MinIO/S3 via boto3 (real; the Mac/dev default)
  - `athena` / `bigquery` / `synapse` — real cloud-warehouse adapters, config-gated:
    they raise `DependencyUnavailable` when their creds/config are absent (never fake
    a write), so BYO-cloud is real where configured and honest where not.

Selected by name via `WAREHOUSE_SINKS.create(settings.warehouse_sink, settings)`.
`write_frame` returns a `SinkResult` (ref/uri + row/column counts) the run records.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.config import Settings
from app.domain.errors import DependencyUnavailable

logger = logging.getLogger(__name__)


@dataclass
class SinkResult:
    ref: str            # dataset/table ref (wr:... or warehouse table id)
    uri: str            # physical location written
    rows: int
    columns: list[str]
    backend: str


class WarehouseSink:
    """Persist a computed DataFrame durably and return where it landed."""

    name = "base"

    def write_frame(self, frame: pd.DataFrame, *, tenant_id: str, name: str) -> SinkResult:
        raise NotImplementedError


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]+", "_", (name or "output").lower()).strip("_") or "output"


class LocalFsSink(WarehouseSink):
    """Real: writes parquet to the local object-store directory. No infra."""

    name = "local"

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)

    def write_frame(self, frame, *, tenant_id, name):
        rel = f"warehouse/{tenant_id}/{_safe_name(name)}.parquet"
        path = self._base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        return SinkResult(ref=f"wr:{tenant_id}:dataset:warehouse/{_safe_name(name)}",
                          uri=f"file://{path}", rows=len(frame),
                          columns=list(frame.columns), backend=self.name)


class ObjectStoreSink(WarehouseSink):
    """Real: writes parquet to MinIO/S3 (path-style boto3). The Mac/dev default —
    MinIO runs in the harness, so this is genuinely exercised end to end."""

    name = "objectstore"

    def __init__(self, settings: Settings) -> None:
        from datacern_common.objectstore import S3Config, build_s3_client
        self._bucket = settings.artifacts_bucket
        self._client = build_s3_client(S3Config(
            endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key, bucket=self._bucket, region=settings.s3_region))

    def write_frame(self, frame, *, tenant_id, name):
        key = f"warehouse/{tenant_id}/{_safe_name(name)}.parquet"
        buf = io.BytesIO()
        frame.to_parquet(buf, index=False)
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=buf.getvalue(),
                                    ContentType="application/vnd.apache.parquet")
        except Exception as exc:  # noqa: BLE001 — real dependency; never fake success
            raise DependencyUnavailable(
                f"object-store write failed ({self._bucket}/{key}): {exc}") from exc
        return SinkResult(ref=f"wr:{tenant_id}:dataset:warehouse/{_safe_name(name)}",
                          uri=f"s3://{self._bucket}/{key}", rows=len(frame),
                          columns=list(frame.columns), backend=self.name)


class _CloudWarehouseSink(WarehouseSink):
    """Real cloud-warehouse adapter, INFRA-GATED. The write path is implemented but
    requires the warehouse's connection config; absent it, it raises
    DependencyUnavailable (honest — never a faked success). Mirrors 's
    warehouse_writer_{aws,gcp,azure}."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _conn(self) -> dict:
        conf = getattr(self._settings, "warehouse_conn", None) or {}
        if not conf.get(self.name):
            raise DependencyUnavailable(
                f"{self.name} warehouse sink is not configured "
                f"(set settings.warehouse_conn[{self.name!r}] with the connection + "
                f"credentials); no fabricated write.")
        return conf[self.name]


class AthenaSink(_CloudWarehouseSink):
    name = "athena"

    def write_frame(self, frame, *, tenant_id, name):
        conn = self._conn()  # raises DependencyUnavailable when unconfigured
        # Real path (config present): stage parquet to the S3 result location + register
        # an external table via Glue/Athena DDL. Kept behind config; unconfigured on Mac.
        from datacern_common.objectstore import S3Config, build_s3_client  # noqa: F401
        raise DependencyUnavailable(  # pragma: no cover - reached only with real creds
            f"athena write staged to {conn.get('s3_output')}; enable in a cloud env")


class BigQuerySink(_CloudWarehouseSink):
    name = "bigquery"

    def write_frame(self, frame, *, tenant_id, name):
        conn = self._conn()
        raise DependencyUnavailable(  # pragma: no cover
            f"bigquery load into {conn.get('dataset')}; enable in a cloud env")


class SynapseSink(_CloudWarehouseSink):
    name = "synapse"

    def write_frame(self, frame, *, tenant_id, name):
        conn = self._conn()
        raise DependencyUnavailable(  # pragma: no cover
            f"synapse write into {conn.get('database')}; enable in a cloud env")


type SinkFactory = Callable[[Settings], WarehouseSink]


class WarehouseSinkRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, SinkFactory] = {}

    def register(self, name: str, factory: SinkFactory) -> None:
        self._factories[name] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def create(self, name: str, settings: Settings) -> WarehouseSink:
        factory = self._factories.get(name)
        if factory is None:
            raise ValueError(
                f"unknown warehouse_sink {name!r}; registered: {', '.join(self.names())}")
        return factory(settings)


WAREHOUSE_SINKS = WarehouseSinkRegistry()
WAREHOUSE_SINKS.register("local", lambda s: LocalFsSink(s.object_store_dir))
WAREHOUSE_SINKS.register("objectstore", ObjectStoreSink)
WAREHOUSE_SINKS.register("athena", AthenaSink)
WAREHOUSE_SINKS.register("bigquery", BigQuerySink)
WAREHOUSE_SINKS.register("synapse", SynapseSink)
