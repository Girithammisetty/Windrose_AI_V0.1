"""MLflow tracking/registry REST client (EXP-FR-001/013/014).

``MlflowClient`` speaks the real MLflow 2.0 REST API against the tracking server
(system of truth). It is used on exactly two synchronous write paths
(experiment create, run-create forwarding) and by the reconciliation sweep
(``search_runs``, ``get_run``). Read endpoints never touch it.

``LocalMlflowClient`` is the in-memory unit-tier double (never wired from
app.main); it lets unit tests seed runs the mirror ingests without a live server.
"""

from __future__ import annotations

import httpx

from app.domain.errors import DependencyUnavailable


class MlflowClient:
    def __init__(self, tracking_uri: str = "http://localhost:5500", *, timeout_s: float = 10.0):
        self.base = tracking_uri.rstrip("/") + "/api/2.0/mlflow"
        self.timeout_s = timeout_s

    async def _post(self, path: str, body: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(f"{self.base}/{path}", json=body)
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(f"MLflow unreachable: {exc}") from exc
        if resp.status_code >= 500:
            raise DependencyUnavailable(f"MLflow error {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise DependencyUnavailable(f"MLflow rejected {path}: {resp.text[:200]}")
        return resp.json()

    async def _get(self, path: str, params: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(f"{self.base}/{path}", params=params)
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(f"MLflow unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise DependencyUnavailable(f"MLflow error {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # -- experiments (synchronous write path, EXP-FR-001) --------------------
    async def create_experiment(self, name: str, tags: dict | None = None) -> str:
        body: dict = {"name": name}
        if tags:
            body["tags"] = [{"key": k, "value": str(v)} for k, v in tags.items()]
        return (await self._post("experiments/create", body))["experiment_id"]

    async def get_experiment(self, experiment_id: str) -> dict:
        return (await self._get("experiments/get", {"experiment_id": experiment_id})).get(
            "experiment", {}
        )

    async def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
        await self._post(
            "experiments/set-experiment-tag",
            {"experiment_id": experiment_id, "key": key, "value": value},
        )

    # -- runs (reconciliation reads, EXP-FR-013) -----------------------------
    async def get_run(self, run_id: str) -> dict:
        return (await self._get("runs/get", {"run_id": run_id})).get("run", {})

    async def search_runs(
        self, experiment_ids: list[str], *, filter_str: str = "",
        max_results: int = 1000, page_token: str | None = None,
    ) -> tuple[list[dict], str | None]:
        body: dict = {"experiment_ids": experiment_ids, "max_results": max_results}
        if filter_str:
            body["filter"] = filter_str
        if page_token:
            body["page_token"] = page_token
        data = await self._post("runs/search", body)
        return data.get("runs", []), data.get("next_page_token")

    # -- write helpers (run-create forwarding + used by integration tests) ---
    async def create_run(self, experiment_id: str, *, start_time: int,
                         tags: dict | None = None, run_name: str | None = None) -> dict:
        body: dict = {"experiment_id": experiment_id, "start_time": start_time}
        if run_name:
            body["run_name"] = run_name
        if tags:
            body["tags"] = [{"key": k, "value": str(v)} for k, v in tags.items()]
        return (await self._post("runs/create", body))["run"]

    async def log_batch(self, run_id: str, *, metrics: list[dict] | None = None,
                        params: list[dict] | None = None, tags: list[dict] | None = None) -> None:
        await self._post("runs/log-batch", {
            "run_id": run_id, "metrics": metrics or [], "params": params or [],
            "tags": tags or [],
        })

    async def update_run(self, run_id: str, *, status: str, end_time: int | None = None) -> None:
        body: dict = {"run_id": run_id, "status": status}
        if end_time is not None:
            body["end_time"] = end_time
        await self._post("runs/update", body)

    async def delete_run(self, run_id: str) -> None:
        await self._post("runs/delete", {"run_id": run_id})

    # -- model registry (EXP-FR-032 governed stage transition) ---------------
    async def ensure_registered_model(self, name: str) -> None:
        try:
            await self._post("registered-models/create", {"name": name})
        except DependencyUnavailable as exc:
            if "RESOURCE_ALREADY_EXISTS" not in str(exc):
                raise

    async def create_model_version(self, name: str, source: str, run_id: str) -> str:
        resp = await self._post(
            "model-versions/create", {"name": name, "source": source, "run_id": run_id})
        return str(resp["model_version"]["version"])

    async def transition_model_version_stage(
        self, name: str, version: str, stage: str, *, archive_existing: bool = False) -> None:
        await self._post("model-versions/transition-stage", {
            "name": name, "version": str(version), "stage": stage,
            "archive_existing_versions": archive_existing})

    async def get_model_version(self, name: str, version: str) -> dict:
        return (await self._get(
            "model-versions/get", {"name": name, "version": str(version)}
        )).get("model_version", {})


class LocalMlflowClient:
    """In-memory unit-tier double (never wired from app.main)."""

    def __init__(self):
        self._experiments: dict[str, dict] = {}
        self._runs: dict[str, dict] = {}
        self._registry: dict[str, dict[str, dict]] = {}  # name -> version -> {current_stage}
        self._seq = 0

    async def create_experiment(self, name: str, tags: dict | None = None) -> str:
        self._seq += 1
        eid = str(self._seq)
        self._experiments[eid] = {"experiment_id": eid, "name": name, "tags": tags or {}}
        return eid

    async def get_experiment(self, experiment_id: str) -> dict:
        return self._experiments.get(experiment_id, {})

    async def set_experiment_tag(self, experiment_id: str, key: str, value: str) -> None:
        self._experiments.setdefault(experiment_id, {}).setdefault("tags", {})[key] = value

    async def get_run(self, run_id: str) -> dict:
        return self._runs.get(run_id, {})

    async def search_runs(self, experiment_ids, *, filter_str="", max_results=1000,
                          page_token=None) -> tuple[list[dict], str | None]:
        runs = [r for r in self._runs.values()
                if r["info"]["experiment_id"] in experiment_ids]
        return runs, None

    async def create_run(self, experiment_id, *, start_time, tags=None, run_name=None) -> dict:
        self._seq += 1
        rid = f"run-{self._seq}"
        run = {
            "info": {"run_id": rid, "run_uuid": rid, "experiment_id": experiment_id,
                     "status": "RUNNING", "start_time": start_time,
                     "artifact_uri": f"s3://mlflow/{experiment_id}/{rid}/artifacts",
                     "run_name": run_name or rid},
            "data": {"metrics": [], "params": [], "tags": []},
        }
        self._runs[rid] = run
        return run

    def seed_run(self, run: dict) -> None:
        self._runs[run["info"]["run_id"]] = run

    async def log_batch(self, run_id, *, metrics=None, params=None, tags=None) -> None:
        run = self._runs[run_id]
        run["data"]["metrics"].extend(metrics or [])
        run["data"]["params"].extend(params or [])
        run["data"]["tags"].extend(tags or [])

    async def update_run(self, run_id, *, status, end_time=None) -> None:
        info = self._runs[run_id]["info"]
        info["status"] = status
        if end_time is not None:
            info["end_time"] = end_time

    async def delete_run(self, run_id) -> None:
        self._runs.pop(run_id, None)

    async def ensure_registered_model(self, name) -> None:
        self._registry.setdefault(name, {})

    async def create_model_version(self, name, source, run_id) -> str:
        versions = self._registry.setdefault(name, {})
        version = str(len(versions) + 1)
        versions[version] = {"version": version, "current_stage": "None",
                             "source": source, "run_id": run_id}
        return version

    async def transition_model_version_stage(self, name, version, stage, *,
                                             archive_existing=False) -> None:
        versions = self._registry.setdefault(name, {})
        if archive_existing:
            for v in versions.values():
                if v.get("current_stage") == stage:
                    v["current_stage"] = "Archived"
        versions.setdefault(str(version), {"version": str(version)})["current_stage"] = stage

    async def get_model_version(self, name, version) -> dict:
        return self._registry.get(name, {}).get(str(version), {})
