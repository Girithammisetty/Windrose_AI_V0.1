"""Real MLflow tracking/registry gateway (BRD §8, BR-15).

The run lifecycle creates the MLflow run BEFORE submitting to the executor, so a run
row never exists without a real MLflow run and no orphan workflow can exist. If MLflow
is unavailable at submit, ``create_run`` raises ``DependencyUnavailable`` (503) and the
run is not created. The local training executor resumes this same run id."""

from __future__ import annotations

import asyncio

from app.domain.errors import DependencyUnavailable


class MlflowGateway:
    def __init__(self, tracking_uri: str, experiment: str):
        self.tracking_uri = tracking_uri
        self.experiment = experiment

    async def create_run(self, *, tags: dict, experiment_id: str | None = None,
                         experiment_name: str | None = None) -> str:
        return await asyncio.to_thread(self._create_sync, tags, experiment_id,
                                       experiment_name)

    def _create_sync(self, tags: dict, experiment_id: str | None = None,
                     experiment_name: str | None = None) -> str:
        try:
            from mlflow.tracking import MlflowClient

            client = MlflowClient(tracking_uri=self.tracking_uri)
            # A retrain run targets the experiment-service experiment so the mirror
            # reconciliation sweep can materialize it (experiment_id is exact; name is
            # a fallback). Otherwise fall back to the shared orchestrator experiment.
            if experiment_id:
                exp_id = experiment_id
            else:
                name = experiment_name or self.experiment
                exp = client.get_experiment_by_name(name)
                exp_id = exp.experiment_id if exp else client.create_experiment(name)
            run = client.create_run(
                exp_id, tags={f"windrose.{k}": str(v) for k, v in tags.items()})
            return run.info.run_id
        except DependencyUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — MLflow down → fail fast (BR-15)
            raise DependencyUnavailable(f"MLflow unavailable: {exc}") from exc

    async def set_terminated(self, run_id: str, status: str) -> None:
        await asyncio.to_thread(self._terminate_sync, run_id, status)

    def _terminate_sync(self, run_id: str, status: str) -> None:
        try:
            from mlflow.tracking import MlflowClient

            MlflowClient(tracking_uri=self.tracking_uri).set_terminated(run_id, status)
        except Exception:  # noqa: BLE001 — terminal bookkeeping is best-effort
            pass
