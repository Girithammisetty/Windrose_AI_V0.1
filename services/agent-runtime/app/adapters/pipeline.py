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
