"""chart-service read adapter — the dashboard-designer's chart-type grounding.

Reads the governed chart-type catalog (CHART-FR-012) via chart-service
``GET /api/v1/chart-types`` under the run's OBO token so the designer only
proposes chart types (+ their config families) that actually exist.

Best-effort, like the semantic reader: any failure logs WARN and returns ``[]``
so the run still produces a proposal (never a silent stub).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("agent-runtime.chartcatalog")


class ChartCatalogClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def list_chart_types(self, *, auth_token: str) -> list[dict]:
        url = f"{self._base}/api/v1/chart-types"
        headers = {"Authorization": f"Bearer {auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("chart-types failed (grounding degraded to empty): err=%r", exc)
            return []
        if resp.status_code != 200:
            logger.warning("chart-types failed (grounding degraded to empty): "
                           "status=%s body=%s", resp.status_code, resp.text[:300])
            return []
        data = resp.json().get("data")
        return data if isinstance(data, list) else []
