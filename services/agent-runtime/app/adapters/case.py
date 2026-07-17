"""case-service read adapter — the triage copilot's grounding source.

Reads a claim case (row-reference triage case) via case-service REST
``GET /api/v1/cases/{id}`` under the run's OBO token. (In the platform target
this read is itself a tool-plane read tool; a direct governed read client is used
here for the grounding step and documented as such in the README.)
"""

from __future__ import annotations

import httpx

from app.domain.errors import NotFound


class CaseServiceClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def get_case(self, *, tenant_id: str, case_id: str, auth_token: str) -> dict:
        url = f"{self._base}/api/v1/cases/{case_id}"
        headers = {"Authorization": f"Bearer {auth_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            raise NotFound(f"case {case_id} not found")
        resp.raise_for_status()
        return resp.json().get("data") or {}
