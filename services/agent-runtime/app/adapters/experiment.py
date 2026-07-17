"""experiment-service read adapter — the inference agent's model grounding source.

Grounds the inference graph on the registered-model catalog + a model's versions
(EXP-FR-052 read surface): (a) ``GET /api/v1/models`` to find the registered model
the request names, and (b) ``GET /api/v1/models/{id}`` to resolve its versions +
stages (so the graph can pick the ``production`` version and read its declared
input schema for feature-compatibility). Both reads run under the run's OBO token.

(In the platform target these reads are themselves tool-plane read tools bound to
experiment-service's MCP facade — ``experiment.models.list`` /
``experiment.model.get``; a direct governed read client is used here for the
grounding step, mirroring the triage copilot's case-service reader.)

Grounding is best-effort: a failing read degrades to an empty context so the agent
still runs — but failures are never SILENT (every non-200 / transport error is
logged WARN with the status).
"""

from __future__ import annotations

import logging
import urllib.parse

import httpx

logger = logging.getLogger("agent-runtime.experiment")


class ExperimentServiceClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def best_runs(self, *, tenant_id: str, algorithm: str, auth_token: str,
                        limit: int = 5) -> list[dict]:
        """Prior MLflow runs for ``algorithm`` (most-recent first, each with its
        metrics/params) — the model-training agent's history grounding via the same
        governed surface as the ``experiment.runs.search`` MCP read tool. RLS scopes
        rows to the tenant server-side. Returns [] on any failure (grounding
        degrades to empty)."""
        query = {"filter[algorithm]": algorithm, "sort": "-created_at",
                 "limit": str(limit)}
        url = f"{self._base}/api/v1/runs?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("experiment runs.search read failed (grounding degraded "
                           "to empty): err=%r", exc)
            return []
        if resp.status_code != 200:
            logger.warning("experiment runs.search read failed (grounding degraded "
                           "to empty): status=%s body=%s", resp.status_code, resp.text[:300])
            return []
        data = resp.json().get("data")
        return data if isinstance(data, list) else []

    async def list_models(self, *, tenant_id: str, auth_token: str,
                          limit: int = 200) -> list[dict]:
        """The registered-model catalog (id/urn/name/model_type/owner). Returns []
        on any failure (grounding degrades to empty)."""
        url = f"{self._base}/api/v1/models"
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers, params={"limit": limit})
        except httpx.HTTPError as exc:
            logger.warning("experiment models.list read failed (grounding degraded "
                           "to empty): err=%r", exc)
            return []
        if resp.status_code != 200:
            logger.warning("experiment models.list read failed (grounding degraded "
                           "to empty): status=%s body=%s", resp.status_code, resp.text[:300])
            return []
        data = resp.json().get("data")
        return data if isinstance(data, list) else []

    async def get_model(self, *, tenant_id: str, model_id: str, auth_token: str) -> dict:
        """A registered model + ALL its versions (each with stage + input_schema).
        Returns {} on any failure (grounding degrades to empty)."""
        url = f"{self._base}/api/v1/models/{model_id}"
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("experiment model.get read failed (grounding degraded to "
                           "empty): err=%r", exc)
            return {}
        if resp.status_code != 200:
            logger.warning("experiment model.get read failed (grounding degraded to "
                           "empty): status=%s body=%s", resp.status_code, resp.text[:300])
            return {}
        return resp.json().get("data") or {}
