"""semantic-service read adapter — the dashboard-designer's grounding source.

Reads the GOVERNED semantic layer (published measures + dimensions) via
semantic-service's MCP read-tool REST facade (SEM-FR-080):
``POST /api/v1/tools/get_metrics`` and ``.../get_dimensions`` under the run's OBO
token. (In the platform target these are tool-plane read tools; a direct governed
read client is used here for the grounding step, mirroring the case reader.)

Grounding is BEST-EFFORT (matches the memory adapter): a 401/403 raises
:class:`GroundingDegraded` so the graph records a visible ``grounding_degraded``
marker; any other failure logs WARN and returns ``[]`` so the run still produces
a proposal (ungrounded, never silently).
"""

from __future__ import annotations

import logging

import httpx

from app.adapters.memory import GroundingDegraded

logger = logging.getLogger("agent-runtime.semantic")


class SemanticLayerClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def _tool(self, tool: str, body: dict, auth_token: str) -> dict:
        url = f"{self._base}/api/v1/tools/{tool}"
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("semantic %s failed (grounding degraded to empty): err=%r",
                           tool, exc)
            return {}
        if resp.status_code in (401, 403):
            logger.warning("semantic %s DENIED (grounding degraded): status=%s body=%s",
                           tool, resp.status_code, resp.text[:300])
            raise GroundingDegraded(resp.status_code, resp.text[:300])
        if resp.status_code != 200:
            logger.warning("semantic %s failed (grounding degraded to empty): "
                           "status=%s body=%s", tool, resp.status_code, resp.text[:300])
            return {}
        return resp.json().get("data") or {}

    async def get_metrics(self, *, tenant_id: str, auth_token: str,
                          workspace_id: str | None = None,
                          model: str | None = None) -> list[dict]:
        body: dict = {}
        if workspace_id:
            body["workspace_id"] = workspace_id
        if model:
            body["model"] = model
        data = await self._tool("get_metrics", body, auth_token)
        metrics = data.get("metrics")
        return metrics if isinstance(metrics, list) else []

    async def get_dimensions(self, *, tenant_id: str, auth_token: str,
                             workspace_id: str | None = None,
                             model: str | None = None) -> list[dict]:
        body: dict = {}
        if workspace_id:
            body["workspace_id"] = workspace_id
        if model:
            body["model"] = model
        data = await self._tool("get_dimensions", body, auth_token)
        dims = data.get("dimensions")
        return dims if isinstance(dims, list) else []

    async def search_verified_queries(self, *, tenant_id: str, auth_token: str,
                                      query: str, workspace_id: str | None = None,
                                      top_k: int = 5) -> list[dict]:
        """SEM-FR-041: ANN over APPROVED verified NL<->SQL pairs (tenant+workspace
        scoped). Grounds the designer with proven query conventions; best-effort
        like get_metrics (401/403 -> GroundingDegraded, other errors -> [])."""
        body: dict = {"q": query, "top_k": top_k}
        if workspace_id:
            body["workspace_id"] = workspace_id
        data = await self._tool("search_verified_queries", body, auth_token)
        results = data.get("results")
        return results if isinstance(results, list) else []
