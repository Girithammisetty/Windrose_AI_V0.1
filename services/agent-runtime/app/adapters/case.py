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

    async def list_cases(self, *, tenant_id: str, workspace_id: str | None,
                         limit: int = 100, auth_token: str) -> list[dict]:
        """Open cases for a workspace — the worklist a decision model batch-runs
        over (DM-FR-060). Each row carries display_projection, so the caller can
        evaluate without a per-case fetch."""
        params = {"limit": limit}
        if workspace_id:
            params["workspace_id"] = workspace_id
        headers = {"Authorization": f"Bearer {auth_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base}/api/v1/cases",
                                    headers=headers, params=params)
        resp.raise_for_status()
        return resp.json().get("data") or []

    async def list_evidence(self, *, tenant_id: str, case_id: str,
                            auth_token: str) -> list[dict]:
        """Evidence attachment metadata for a case (task #77 GET
        /cases/{id}/evidence). Metadata only — no bytes; requires
        case.evidence.read. Each row: {id, filename, content_type, size_bytes,
        uploaded_by, created_at}."""
        url = f"{self._base}/api/v1/cases/{case_id}/evidence"
        headers = {"Authorization": f"Bearer {auth_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code in (401, 403):
            # Agent principal lacks case.evidence.read: proceed ungrounded on
            # documents rather than failing the whole run (caller records it).
            return []
        resp.raise_for_status()
        return resp.json().get("data") or []

    async def download_evidence(self, *, tenant_id: str, case_id: str,
                                evidence_id: str, auth_token: str) -> tuple[bytes, str]:
        """Raw bytes + content-type of one evidence file (GET
        /cases/{id}/evidence/{eid}/download). Streams the object from MinIO via
        case-service; requires case.evidence.read."""
        url = (f"{self._base}/api/v1/cases/{case_id}"
               f"/evidence/{evidence_id}/download")
        headers = {"Authorization": f"Bearer {auth_token}"}
        # Evidence blobs can be up to 25 MiB (case-service cap); give downloads
        # a longer budget than a metadata read.
        async with httpx.AsyncClient(timeout=max(self._timeout, 30.0)) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    async def list_dispositions(self, *, tenant_id: str, auth_token: str) -> list[dict]:
        """All dispositions for the caller's workspace (case-service's real
        ListDispositions has no active-only filter — the caller filters).
        case.apply_disposition's real input schema requires a disposition_id
        (a real row, not free text), so the triage copilot must ground its
        choice in this catalog rather than inventing a code (confirmed live
        2026-07-17: every prior triage proposal failed tool-plane schema
        validation because disposition_id was never supplied)."""
        url = f"{self._base}/api/v1/dispositions"
        headers = {"Authorization": f"Bearer {auth_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return [d for d in (resp.json().get("data") or []) if d.get("active")]
