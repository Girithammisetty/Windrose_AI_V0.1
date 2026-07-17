"""Real model registry adapter over the MLflow tracking/registry server.

Resolves registered model versions (INF-FR-002.1): existence, stage
(Production/Staging/Archived/None), and the input schema taken from the model's
logged **signature** (the MLflow equivalent of experiment-service's
``input_schema``). Blocking MLflow client calls run in a worker thread.
"""

from __future__ import annotations

import asyncio
import os

from app.domain.ports import ResolvedModel
from app.domain.schema_compat import ModelInputColumn


def set_global_mlflow_uri(tracking_uri: str) -> None:
    """Set MLflow's GLOBAL tracking + registry uri. ``models:/<name>/<stage>``
    resolution (get_model_info, pyfunc.load_model) and the model registry APIs read
    these process globals — without them MLflow silently falls back to the local
    ``./mlruns`` file store and raises ``Registered Model not found``. Called from
    the registry adapter, the scoring executor, and app startup (from config)."""
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_registry_uri(tracking_uri)


class MlflowModelRegistry:
    def __init__(
        self,
        tracking_uri: str = "http://localhost:5500",
        *,
        s3_endpoint_url: str = "http://localhost:9000",
        s3_access_key: str = "windrose",
        s3_secret_key: str = "windrose_dev",
    ) -> None:
        self.tracking_uri = tracking_uri
        # MLflow downloads model artifacts from MinIO via boto3 — configure the S3
        # endpoint + creds so ``models:/`` resolution works against local infra.
        os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", s3_endpoint_url)
        os.environ.setdefault("AWS_ACCESS_KEY_ID", s3_access_key)
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", s3_secret_key)
        # BUG-2: ``models:/<name>/<stage>`` artifact + registry resolution
        # (get_model_info, pyfunc.load_model) uses MLflow's GLOBAL uri, not a
        # client instance. Set it globally from config so resolution hits the real
        # MLflow server rather than the default local ./mlruns file store.
        set_global_mlflow_uri(tracking_uri)

    def _client(self):
        from mlflow.tracking import MlflowClient

        return MlflowClient(tracking_uri=self.tracking_uri)

    async def resolve_version(self, name: str, version: int) -> ResolvedModel:
        return await asyncio.to_thread(self._resolve_version_sync, name, version)

    def _resolve_version_sync(self, name: str, version: int) -> ResolvedModel:
        from mlflow.exceptions import MlflowException

        set_global_mlflow_uri(self.tracking_uri)  # models:/ resolution uses the global uri
        client = self._client()
        try:
            mv = client.get_model_version(name, str(version))
        except MlflowException as exc:
            raise LookupError(f"{name}@{version}: {exc}") from exc
        stage = (mv.current_stage or "None").strip().lower()
        model_uri = f"models:/{name}/{version}"
        inputs = self._load_inputs(model_uri)
        return ResolvedModel(
            name=name, version=int(version), stage=stage, model_uri=model_uri,
            inputs=inputs, model_id=name, run_id=mv.run_id,
        )

    async def resolve_by_stage(self, name: str, stage: str) -> ResolvedModel | None:
        return await asyncio.to_thread(self._resolve_by_stage_sync, name, stage)

    def _resolve_by_stage_sync(self, name: str, stage: str) -> ResolvedModel | None:
        set_global_mlflow_uri(self.tracking_uri)
        client = self._client()
        mlflow_stage = {"production": "Production", "staging": "Staging",
                        "archived": "Archived", "none": "None"}.get(stage.lower(), stage)
        versions = client.get_latest_versions(name, stages=[mlflow_stage])
        if not versions:
            return None
        mv = max(versions, key=lambda v: int(v.version))
        model_uri = f"models:/{name}/{mv.version}"
        return ResolvedModel(
            name=name, version=int(mv.version), stage=stage, model_uri=model_uri,
            inputs=self._load_inputs(model_uri), model_id=name, run_id=mv.run_id,
        )

    def _load_inputs(self, model_uri: str) -> list[ModelInputColumn]:
        from mlflow.models import get_model_info

        set_global_mlflow_uri(self.tracking_uri)  # get_model_info reads the global uri
        info = get_model_info(model_uri)
        sig = info.signature
        if sig is None or sig.inputs is None:
            return []
        cols: list[ModelInputColumn] = []
        for spec in sig.inputs.to_dict():
            name = spec.get("name")
            if name is None:
                continue
            col_type = spec.get("type", "string")
            required = bool(spec.get("required", True))
            cols.append(ModelInputColumn(name=str(name), type=str(col_type), required=required))
        return cols
