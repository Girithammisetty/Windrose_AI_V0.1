"""realtime-hub publish adapter (ART-FR-070/071): the runtime publishes run
stream events (token, tool_call_*, proposal_created, run_completed, done) to the
hub topic ``agent_run:<run_id>``; clients consume with reconnect/replay.

Wire contract (services/realtime-hub/internal/api/internal_publish.go):
  POST {internal}/internal/v1/publish
  Authorization: Bearer <service/agent JWT carrying scope realtime.publish>
  {"tenant_id": ..., "topic": ..., "event_id": <uuid, idempotency key>,
   "payload_json": <raw JSON payload>, "ttl_seconds": <replay-buffer TTL>}

``payload_json`` is decoded as ``json.RawMessage`` and fanned out verbatim as
the SSE ``data:`` frame, so it MUST be the payload object itself (the browser
does ``JSON.parse(ev.data)`` and reads ``data.type``/``data.text``). The event
semantic therefore rides in ``payload_json.type``. ``ttl_seconds`` keeps the
event in the hub replay ring so a client that subscribes a beat after the run
started (the copilot always does) still receives it.

Publish is fire-and-forget relative to the run — a hub outage never fails the
run — but failures are LOGGED (never silently swallowed) so a misconfigured
hub URL or missing publish scope is visible.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

import httpx

logger = logging.getLogger("agent-runtime.realtime")

# Keep stream events replayable for the copilot subscribe window (matches the
# hub's 10-minute chat/agent_run replay ring, RTH-FR-034).
DEFAULT_TTL_SECONDS = 600


class RealtimeHubClient:
    def __init__(
        self,
        internal_url: str,
        *,
        token_provider: Callable[[str], str] | None = None,
        timeout_s: float = 5.0,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._base = internal_url.rstrip("/")
        self._token_provider = token_provider
        self._timeout = timeout_s
        self._default_ttl = default_ttl_seconds

    async def publish(
        self,
        *,
        topic: str,
        event: str,
        data: dict,
        tenant_id: str,
        ttl_seconds: int | None = None,
    ) -> None:
        url = f"{self._base}/internal/v1/publish"
        # The hub fans payload_json out verbatim; the semantic type must ride
        # inside it (ui-web keys off data.type containing token/done/...).
        payload = {"type": event, **(data or {})}
        body = {
            "tenant_id": tenant_id,
            "topic": topic,
            "event_id": str(uuid.uuid4()),
            "payload_json": payload,
            "ttl_seconds": self._default_ttl if ttl_seconds is None else ttl_seconds,
        }
        headers = {}
        try:
            if self._token_provider is not None:
                # Service JWT with scope realtime.publish (hub authenticatePublisher).
                headers["Authorization"] = f"Bearer {self._token_provider(tenant_id)}"
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "realtime-hub publish failed (non-fatal): topic=%s event=%s "
                    "status=%s body=%s", topic, event, resp.status_code,
                    resp.text[:500])
        except Exception as exc:  # noqa: BLE001 — best-effort, run unaffected
            logger.warning(
                "realtime-hub publish error (non-fatal): topic=%s event=%s err=%r",
                topic, event, exc)
