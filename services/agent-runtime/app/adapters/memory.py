"""memory-service RAG retrieval adapter (grounding for the triage copilot).

Retrieves relevant resolved-case memories to ground triage reasoning. Retrieval
is best-effort grounding: if memory-service is unreachable the agent still runs
(ungrounded) — but failures are never SILENT:

* every non-200 / transport failure is logged WARN with the status, and
* an authorization failure (401/403 — a broken service credential, not a
  transient blip) raises :class:`GroundingDegraded` so the calling graph can
  record a ``grounding_degraded`` marker in the run trace/state.

``[]`` is returned only for genuine empty result sets (and, after logging, for
transient failures where an empty grounding context is the sound fallback).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("agent-runtime.memory")


class GroundingDegraded(Exception):
    """memory-service refused the credential (401/403): grounding is degraded
    for a structural reason that must surface in the run trace."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"memory-service retrieval denied: {status_code} {detail}")


class MemoryServiceClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    async def retrieve(
        self, *, tenant_id: str, query: str, auth_token: str, top_k: int = 5,
        snapshot_ver: str | None = None,
    ) -> list[dict]:
        # memory-service contract (see memory-service app/api/routes/memories.py
        # + app/api/schemas.py RetrieveIn): endpoint is POST /api/v1/retrieve, the
        # query field is `query_text`, and the response envelope's `data` is a LIST
        # of result items (each with `content`/`score`/`kind`), not `{"results": [...]}`.
        url = f"{self._base}/api/v1/retrieve"
        headers = {"Authorization": f"Bearer {auth_token}"}
        # Only `tenant` scope: the server resolves its scope_ref from the principal's
        # tenant_id, so no scope_refs are needed. A `workspace` scope would require
        # scope_refs.workspace (else the server returns 400), and this grounding path
        # has no workspace context to supply.
        body: dict = {"query_text": query, "top_k": top_k, "scopes": ["tenant"]}
        # Replay grounding (ART-FR-015): pin retrieval to a corpus snapshot so the
        # candidate reproduces what the agent WOULD have retrieved at that snapshot
        # (memory-service RetrieveIn.snapshot_ver), not live memory.
        if snapshot_ver:
            body["snapshot_ver"] = snapshot_ver
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "memory-service retrieve failed (grounding degraded to empty): err=%r",
                exc)
            return []
        if resp.status_code in (401, 403):
            logger.warning(
                "memory-service retrieve DENIED (grounding degraded): status=%s body=%s",
                resp.status_code, resp.text[:300])
            raise GroundingDegraded(resp.status_code, resp.text[:300])
        if resp.status_code != 200:
            logger.warning(
                "memory-service retrieve failed (grounding degraded to empty): "
                "status=%s body=%s", resp.status_code, resp.text[:300])
            return []
        data = resp.json().get("data")
        return data if isinstance(data, list) else []
