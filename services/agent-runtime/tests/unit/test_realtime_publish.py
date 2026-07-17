"""Contract tests for the realtime-hub internal-publish adapter (RTH-FR-021).

Pins the wire contract in services/realtime-hub/internal/api/internal_publish.go:
  * endpoint POST {internal}/internal/v1/publish (NOT /internal/publish),
  * Authorization: Bearer <service JWT with scope realtime.publish>,
  * body {tenant_id, topic, event_id (uuid), payload_json, ttl_seconds},
  * payload_json is the payload OBJECT itself (fanned out verbatim as the SSE
    data frame; ui-web JSON.parses it and reads data.type / data.text),
  * failures are logged WARN with status/body but never raise (a hub outage
    must not fail the run).

Hermetic: httpx MockTransport stands in for the hub.
"""

from __future__ import annotations

import functools
import json
import logging
import uuid

import httpx

import app.adapters.realtime as rt_mod
from app.adapters.realtime import RealtimeHubClient


def _patch_transport(monkeypatch, handler) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)
    monkeypatch.setattr(
        rt_mod.httpx, "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=transport))
    return captured


async def test_publish_hits_internal_v1_with_auth_and_correct_body(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"event_id": "e", "accepted": True, "reason": ""})

    captured = _patch_transport(monkeypatch, handler)
    client = RealtimeHubClient(
        "http://localhost:8315",
        token_provider=lambda tenant: f"svc-token-for-{tenant}")

    await client.publish(topic="agent_run:r-1", event="token",
                         data={"text": "hello"}, tenant_id="t-42")

    req = captured[0]
    assert req.url.path == "/internal/v1/publish"
    assert req.headers["authorization"] == "Bearer svc-token-for-t-42"
    body = json.loads(req.content)
    assert body["tenant_id"] == "t-42"
    assert body["topic"] == "agent_run:r-1"
    uuid.UUID(body["event_id"])  # idempotency key is a real uuid
    assert body["ttl_seconds"] == 600  # replayable for the subscribe window
    # payload_json is the payload OBJECT with the semantic type inside it —
    # the hub fans it out verbatim and the browser reads data.type/data.text.
    assert body["payload_json"] == {"type": "token", "text": "hello"}


async def test_publish_failure_logs_warning_with_status_and_body(monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"accepted": False, "reason": "UNAUTHENTICATED: producer token required"})

    _patch_transport(monkeypatch, handler)
    client = RealtimeHubClient("http://localhost:8315",
                               token_provider=lambda t: "tok")

    with caplog.at_level(logging.WARNING, logger="agent-runtime.realtime"):
        await client.publish(topic="agent_run:r-1", event="run_completed",
                             data={}, tenant_id="t-42")  # must NOT raise

    assert any("401" in r.message and "UNAUTHENTICATED" in r.message
               for r in caplog.records), "publish failure must be logged, not swallowed"


async def test_publish_transport_error_is_nonfatal_but_logged(monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("hub down", request=request)

    _patch_transport(monkeypatch, handler)
    client = RealtimeHubClient("http://localhost:8315",
                               token_provider=lambda t: "tok")

    with caplog.at_level(logging.WARNING, logger="agent-runtime.realtime"):
        await client.publish(topic="agent_run:r-1", event="done", data={},
                             tenant_id="t-42")

    assert any("hub down" in r.message or "ConnectError" in r.message
               for r in caplog.records)
