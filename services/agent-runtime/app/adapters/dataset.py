"""dataset-service read adapter — the inference agent's dataset grounding source.

Grounds the inference graph on (a) the dataset catalog (``GET /api/v1/datasets``,
optionally filtered by a free-text ``q``) to find the input dataset the request
names, and (b) that dataset's current-version schema + row_count
(``GET /api/v1/datasets/{id}/versions``, newest version) so the ``check`` node can
validate dataset<->model feature compatibility. Both reads run under the run's OBO
token.

(In the platform target these reads are themselves tool-plane read tools bound to
dataset-service's MCP facade — ``dataset.get`` / ``dataset.profile.get``; a direct
governed read client is used here for the grounding step, mirroring the triage
copilot's case-service reader.)

Grounding is best-effort: a failing read degrades to an empty context so the agent
still runs — but failures are never SILENT (every non-200 / transport error is
logged WARN with the status).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("agent-runtime.dataset")


class DatasetServiceClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def list_datasets(self, *, tenant_id: str, auth_token: str,
                            q: str | None = None, limit: int = 200) -> list[dict]:
        """The dataset catalog (id/urn/name/status/lifecycle/created_at). Returns []
        on any failure (grounding degrades to empty)."""
        url = f"{self._base}/api/v1/datasets"
        headers = {"Authorization": f"Bearer {auth_token}"}
        params: dict = {"limit": limit, "sort": "-created_at"}
        if q:
            params["q"] = q
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            logger.warning("dataset list read failed (grounding degraded to empty): "
                           "err=%r", exc)
            return []
        if resp.status_code != 200:
            logger.warning("dataset list read failed (grounding degraded to empty): "
                           "status=%s body=%s", resp.status_code, resp.text[:300])
            return []
        data = resp.json().get("data")
        return data if isinstance(data, list) else []

    async def get_schema(self, *, tenant_id: str, dataset_id: str,
                         auth_token: str) -> dict:
        """The dataset's newest version schema + row_count for compatibility
        checking. Returns ``{"version_no": None, "schema": {}, "row_count": None}``
        on any failure (grounding degrades to an empty schema)."""
        empty = {"version_no": None, "schema": {}, "row_count": None}
        url = f"{self._base}/api/v1/datasets/{dataset_id}/versions"
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers, params={"limit": 200})
        except httpx.HTTPError as exc:
            logger.warning("dataset schema read failed (grounding degraded to empty): "
                           "err=%r", exc)
            return empty
        if resp.status_code != 200:
            logger.warning("dataset schema read failed (grounding degraded to empty): "
                           "status=%s body=%s", resp.status_code, resp.text[:300])
            return empty
        versions = resp.json().get("data") or []
        if not isinstance(versions, list) or not versions:
            return empty
        # Newest version = highest version_no (list sort is not guaranteed here).
        latest = max(versions, key=lambda v: v.get("version_no") or 0)
        return {"version_no": latest.get("version_no"),
                "schema": latest.get("schema") or {},
                "row_count": latest.get("row_count")}
