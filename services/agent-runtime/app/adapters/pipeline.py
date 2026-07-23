"""pipeline-orchestrator read adapter — the model-training agent's grounding
source for the algorithm catalog + template parameter schema.

Reads the algorithm-template catalog and a specific algorithm's parameter schema
via pipeline-orchestrator REST (``GET /api/v1/algorithm-templates[/{name}]``)
under the run's OBO token. (In the platform target these are governed tool-plane
read tools — the ``pipeline.components.list`` / algorithm-template catalog
surface; a direct governed read client is used here for the grounding step and
documented as such, mirroring the triage copilot's case reader.)

Grounding is best-effort: a non-200 / transport failure is logged WARN and
returns an empty result rather than failing the run — the calling graph records
the degradation in its trace (never silent).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("agent-runtime.pipeline")


class PipelineOrchestratorClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def list_algorithms(self, *, tenant_id: str, auth_token: str) -> list[dict]:
        data = await self._get("/api/v1/algorithm-templates", auth_token)
        return data if isinstance(data, list) else []

    async def get_algorithm(self, *, tenant_id: str, algorithm: str,
                            auth_token: str) -> dict:
        data = await self._get(f"/api/v1/algorithm-templates/{algorithm}", auth_token)
        return data if isinstance(data, dict) else {}

    async def list_components(self, *, tenant_id: str, auth_token: str) -> list[dict]:
        """The data-prep operator catalog (BRD 62) — grounding for the
        data_pipeline_builder agent. ``GET /api/v1/components``."""
        data = await self._get("/api/v1/components", auth_token)
        return data if isinstance(data, list) else []

    async def get_run(self, *, tenant_id: str, run_id: str, auth_token: str) -> dict:
        """One pipeline run's status/metrics/model refs (``GET /api/v1/runs/{id}``)
        — the ml-engineer agent's poll surface. {} on failure (degrades, never
        raises; the graph treats a vanished run as a failed candidate)."""
        data = await self._get(f"/api/v1/runs/{run_id}", auth_token)
        return data if isinstance(data, dict) else {}

    async def _get(self, path: str, auth_token: str):
        url = self._base + path
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("pipeline read failed (grounding degraded to empty): "
                           "path=%s err=%r", path, exc)
            return None
        if resp.status_code != 200:
            logger.warning("pipeline read non-200 (grounding degraded to empty): "
                           "path=%s status=%s body=%s", path, resp.status_code,
                           resp.text[:200])
            return None
        return resp.json().get("data")


class TrainingLaunchFailed(Exception):
    """A training-pipeline launch was rejected (status + body preserved so the
    graph can report the real reason instead of a vague failure)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"training launch failed ({status}): {detail}")
        self.status = status
        self.detail = detail


class PipelineWriter:
    """The ml-engineer agent's SANDBOXED write surface (BRD 52 MLE-FR-020):
    instantiate+launch a train-mode pipeline via the SAME REST route the UI
    uses (``POST /api/v1/algorithm-templates/{name}/pipelines``), authorized by
    the OBO user's own ``pipeline.template.create`` — reversible artifacts only,
    identical authz + audit to a human click. The one consequential action
    (model promotion) never goes through here; it is a WriteIntent → proposal.
    Unlike the read adapters this RAISES on failure: launching is a step the
    graph must handle honestly, not degrade silently."""

    def __init__(self, base_url: str, *, timeout_s: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def instantiate(self, *, tenant_id: str, algorithm: str, auth_token: str,
                          dataset_refs: dict, params: dict,
                          workspace_id: str | None = None,
                          name: str | None = None, mode: str = "train") -> dict:
        """Two steps, one call for the graph: (1) create the pipeline TEMPLATE
        from the algorithm (``POST /algorithm-templates/{name}/pipelines`` — this
        route only materializes the template), then (2) SUBMIT a run of it
        (``POST /pipelines/{template_id}/run``). Returns the run payload (whose
        ``id`` the graph then polls). Raises TrainingLaunchFailed with the real
        status/body on either step so the graph reports honestly."""
        headers = {"Authorization": f"Bearer {auth_token}",
                   "Content-Type": "application/json"}
        template_body: dict = {"mode": mode, "dataset_refs": dataset_refs,
                               "parameters": params}
        if workspace_id:
            template_body["workspace_id"] = workspace_id
        if name:
            template_body["name"] = name
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                tresp = await client.post(
                    f"{self._base}/api/v1/algorithm-templates/{algorithm}/pipelines",
                    headers=headers, json=template_body)
                if tresp.status_code not in (200, 201, 202):
                    raise TrainingLaunchFailed(tresp.status_code, tresp.text[:300])
                template = (tresp.json().get("data") or {})
                template_id = template.get("id")
                if not template_id:
                    raise TrainingLaunchFailed(tresp.status_code,
                                               "instantiate returned no template id")
                rresp = await client.post(
                    f"{self._base}/api/v1/pipelines/{template_id}/run",
                    headers=headers, json={"run_parameters": {}})
        except httpx.HTTPError as exc:
            raise TrainingLaunchFailed(0, repr(exc)) from exc
        if rresp.status_code not in (200, 201, 202):
            raise TrainingLaunchFailed(rresp.status_code, rresp.text[:300])
        run = (rresp.json().get("data") or {})
        return run if isinstance(run, dict) else {}
