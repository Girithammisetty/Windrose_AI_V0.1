"""Real local scoring executor (INF-FR-004/040).

Loads the registered model from MLflow, reads the real input parquet from object
storage, runs ``model.predict`` on the real data, and writes the scored output as
a **single** parquet object (single-snapshot commit — no partial results are ever
visible; the object is created atomically at completion). Returns a pointer to
the output so the finalize step can register it as an output dataset version.

This is the local, Mac-testable real substitute for the pipeline-orchestrator /
Argo scoring run. It speaks the real MLflow + S3 wire protocols end to end.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from urllib.parse import urlparse

import pyarrow as pa
import pyarrow.parquet as pq

from app.domain.ports import ResolvedDataset, ResolvedModel, ScoringResult

_SYS_JOB = "_windrose_job_id"
_SYS_MODEL = "_windrose_model_version"
_SYS_SCORED = "_scored_at"


class LocalScoringExecutor:
    def __init__(
        self,
        *,
        datasets_bucket: str = "windrose-datasets",
        s3_endpoint_url: str = "http://localhost:9000",
        s3_access_key: str = "windrose",
        s3_secret_key: str = "windrose_dev",
        s3_region: str = "us-east-1",
        mlflow_tracking_uri: str = "http://localhost:5500",
    ) -> None:
        from windrose_common.objectstore import S3Config, build_s3_client

        self._bucket = datasets_bucket
        self._cfg = S3Config(
            endpoint_url=s3_endpoint_url, access_key=s3_access_key,
            secret_key=s3_secret_key, bucket=datasets_bucket, region=s3_region)
        self._s3 = build_s3_client(self._cfg)
        self._mlflow_uri = mlflow_tracking_uri

    async def run(self, *, model: ResolvedModel, dataset: ResolvedDataset, job,
                  parameters: dict) -> ScoringResult:
        return await asyncio.to_thread(self._run_sync, model, dataset, job, parameters or {})

    def _run_sync(self, model: ResolvedModel, dataset: ResolvedDataset, job,
                  parameters: dict) -> ScoringResult:
        import mlflow

        from app.adapters.mlflow_registry import set_global_mlflow_uri

        # ``models:/`` load uses MLflow's GLOBAL uri (BUG-2) — set tracking+registry.
        set_global_mlflow_uri(self._mlflow_uri)
        loaded = mlflow.pyfunc.load_model(model.model_uri)

        df = self._read_parquet(dataset.storage_uri)
        input_cols = [c.name for c in model.inputs] or list(df.columns)
        features = df[[c for c in input_cols if c in df.columns]]
        preds = loaded.predict(features)

        out = df.copy() if parameters.get("include_features", True) else features.copy()
        pred_series = _as_series(preds, len(out))
        out["prediction"] = pred_series
        out[_SYS_JOB] = job.id
        out[_SYS_MODEL] = f"{model.name}@{model.version}"
        out[_SYS_SCORED] = _now_iso()

        key = f"scores/{job.tenant_id}/{job.id}/part-0.parquet"
        self._write_parquet(key, out)
        return ScoringResult(
            output_storage_uri=f"s3://{self._bucket}/{key}",
            snapshot_id=uuid.uuid4().hex,
            row_count=int(len(out)),
            prediction_columns=["prediction"],
        )

    def _read_parquet(self, storage_uri: str):
        bucket, key = _parse_s3(storage_uri)
        obj = self._s3.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        table = pq.read_table(io.BytesIO(data))
        return table.to_pandas()

    def _write_parquet(self, key: str, df) -> None:
        table = pa.Table.from_pandas(df, preserve_index=False)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        buf.seek(0)
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=buf.getvalue(),
                            ContentType="application/octet-stream")


def _parse_s3(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"expected s3:// uri, got {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def _as_series(preds, n: int):
    import numpy as np
    import pandas as pd

    if isinstance(preds, pd.DataFrame):
        return preds.iloc[:, 0].reset_index(drop=True)
    if isinstance(preds, pd.Series):
        return preds.reset_index(drop=True)
    arr = np.asarray(preds).reshape(n, -1)[:, 0]
    return pd.Series(arr)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
