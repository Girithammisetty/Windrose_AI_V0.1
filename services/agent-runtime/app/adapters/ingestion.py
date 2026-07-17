"""ingestion-service read adapter — the onboarding agent's grounding source.

Grounds the onboarding graph on (a) the connector-type catalog (ING-FR-002,
``GET /api/v1/connector-types``) and (b) a saved connection's previewed source
schema (ING-FR-005, ``POST /api/v1/connections/{id}/preview``), both under the
run's OBO token. (In the platform target these reads are themselves tool-plane
read tools bound to ingestion-service's MCP facade; a direct governed read
client is used here for the grounding step, mirroring the triage copilot's
case-service reader.)

Grounding is best-effort: a failing read degrades to an empty context so the
agent still runs — but failures are never SILENT (every non-200 / transport
error is logged WARN with the status).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("agent-runtime.ingestion")


class IngestionServiceClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def connector_types(self, *, tenant_id: str, auth_token: str) -> list[dict]:
        """The connector-type catalog (display name, category, field schema per
        type). Returns [] on any failure (grounding degrades to empty)."""
        url = f"{self._base}/api/v1/connector-types"
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("ingestion connector-types read failed (grounding "
                           "degraded to empty): err=%r", exc)
            return []
        if resp.status_code != 200:
            logger.warning("ingestion connector-types read failed (grounding "
                           "degraded to empty): status=%s body=%s",
                           resp.status_code, resp.text[:300])
            return []
        data = resp.json().get("data")
        return data if isinstance(data, list) else []

    async def preview(self, *, tenant_id: str, connection_id: str, auth_token: str,
                      table: str | None = None, path: str | None = None,
                      query: str | None = None, limit: int = 50) -> dict:
        """Preview a saved connection's source schema (<=100 rows, never
        persisted). Returns {} when no connection/target is supplied or on any
        failure — the agent grounds on the connector catalog + memory instead."""
        if not connection_id or not (table or path or query):
            return {}
        url = f"{self._base}/api/v1/connections/{connection_id}/preview"
        headers = {"Authorization": f"Bearer {auth_token}"}
        body: dict = {"limit": limit}
        if table:
            body["table"] = table
        if path:
            body["path"] = path
        if query:
            body["query"] = query
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("ingestion connection preview failed (grounding "
                           "degraded to empty): err=%r", exc)
            return {}
        if resp.status_code != 200:
            logger.warning("ingestion connection preview failed (grounding "
                           "degraded to empty): status=%s body=%s",
                           resp.status_code, resp.text[:300])
            return {}
        return resp.json().get("data") or {}
