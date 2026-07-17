"""Contract test for the memory-service RAG retrieval adapter.

Pins the wire contract the triage copilot depends on for grounding
(memory-service app/api/routes/memories.py + app/api/schemas.py RetrieveIn):
  - endpoint is POST /api/v1/retrieve (NOT /api/v1/memories/retrieve),
  - the query field is `query_text` (NOT `query`),
  - the response envelope's `data` is a LIST of result items (each with
    `content`/`score`/`kind`), read directly (NOT `data["results"]`).

A regression on any of these makes memory-service return 404/405/400 (or the
parse yield nothing) and grounding silently degrades to empty. Hermetic: an
httpx MockTransport stands in for memory-service, so no infra is required.
"""

from __future__ import annotations

import functools
import json
import logging

import httpx
import pytest

import app.adapters.memory as memory_mod
from app.adapters.memory import GroundingDegraded, MemoryServiceClient


def _patch_transport(monkeypatch, handler) -> list[httpx.Request]:
    """Route the adapter's internally-constructed AsyncClient through a
    MockTransport, capturing every request it sends."""
    captured: list[httpx.Request] = []

    def _wrapped_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped_handler)
    monkeypatch.setattr(
        memory_mod.httpx,
        "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=transport),
    )
    return captured


def _envelope(data, **extra) -> dict:
    """Mirror memory-service app/api/schemas.py:data_envelope."""
    return {"data": data, **extra}


async def test_retrieve_uses_correct_path_field_and_reads_list(monkeypatch):
    results = [
        {"kind": "memory", "content": "resolved: duplicate-invoice fraud -> SIU, high",
         "score": 0.91, "content_disposition": "untrusted"},
        {"kind": "rag", "content": "policy clause 4.2 on duplicate submissions",
         "score": 0.77, "content_disposition": "untrusted"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        # Path must be the router-prefixed /api/v1/retrieve.
        assert request.url.path == "/api/v1/retrieve"
        body = json.loads(request.content)
        # RetrieveIn.query_text — the old `query` field would be dropped by the model.
        assert body["query_text"] == "triage claim c-501 amount 12500 merchant ACME"
        assert "query" not in body
        assert body["top_k"] == 5
        # `tenant` scope is server-resolved from the principal (no scope_refs needed);
        # a `workspace` scope with no scope_refs.workspace would 400.
        assert body["scopes"] == ["tenant"]
        assert request.headers["authorization"] == "Bearer tok-123"
        return httpx.Response(200, json=_envelope(results, degraded=False))

    _patch_transport(monkeypatch, handler)
    client = MemoryServiceClient("http://memory-service:8080/")

    out = await client.retrieve(
        tenant_id="t-1",
        query="triage claim c-501 amount 12500 merchant ACME",
        auth_token="tok-123",
        top_k=5,
    )

    # `data` is the list itself — not `data["results"]`.
    assert out == results
    assert [m["content"] for m in out][0].startswith("resolved:")


async def test_retrieve_non_200_degrades_to_empty(monkeypatch):
    """A 404 (the pre-fix symptom of the wrong path) must degrade to empty
    grounding, never raise."""
    captured = _patch_transport(
        monkeypatch, lambda req: httpx.Response(404, json={"error": "not found"})
    )
    client = MemoryServiceClient("http://memory-service:8080")

    out = await client.retrieve(
        tenant_id="t-1", query="q", auth_token="tok", top_k=5)

    assert out == []
    assert captured[0].url.path == "/api/v1/retrieve"


async def test_retrieve_transport_error_degrades_to_empty(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("memory-service unreachable", request=request)

    _patch_transport(monkeypatch, handler)
    client = MemoryServiceClient("http://memory-service:8080")

    out = await client.retrieve(
        tenant_id="t-1", query="q", auth_token="tok", top_k=5)

    assert out == []


async def test_retrieve_empty_list_and_missing_data_are_safe(monkeypatch):
    """Empty result set and a malformed (non-list) `data` both yield []."""
    for payload in (_envelope([]), {"data": {"results": []}}, {}):
        _patch_transport(monkeypatch, lambda req, p=payload: httpx.Response(200, json=p))
        client = MemoryServiceClient("http://memory-service:8080")
        out = await client.retrieve(
            tenant_id="t-1", query="q", auth_token="tok", top_k=5)
        assert out == []


async def test_retrieve_non_200_logs_warning(monkeypatch, caplog):
    """Failures degrade to [] but are never SILENT: a WARN with the status is
    logged so a broken grounding path is visible."""
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, json={"error": "boom"}))
    client = MemoryServiceClient("http://memory-service:8080")

    with caplog.at_level(logging.WARNING, logger="agent-runtime.memory"):
        out = await client.retrieve(tenant_id="t-1", query="q", auth_token="tok")

    assert out == []
    assert any("500" in r.message for r in caplog.records)


@pytest.mark.parametrize("status", [401, 403])
async def test_retrieve_auth_failure_raises_grounding_degraded(monkeypatch, status, caplog):
    """401/403 is a structural credential failure, not a transient blip — it
    raises GroundingDegraded so the graph can record the marker in the trace."""
    _patch_transport(monkeypatch, lambda req: httpx.Response(status, json={"error": "denied"}))
    client = MemoryServiceClient("http://memory-service:8080")

    with caplog.at_level(logging.WARNING, logger="agent-runtime.memory"):
        with pytest.raises(GroundingDegraded) as exc:
            await client.retrieve(tenant_id="t-1", query="q", auth_token="tok")

    assert exc.value.status_code == status
    assert any(str(status) in r.message for r in caplog.records)


async def test_triage_graph_records_grounding_degraded_marker():
    """The triage ground node catches GroundingDegraded, proceeds ungrounded,
    and records a visible grounding_degraded marker in the run trace/state."""
    from app.adapters.fakes import FakeCaseReader, FakeLlm
    from app.graphs.base import GraphDeps
    from app.graphs.triage import run_triage

    class DeniedMemory:
        async def retrieve(self, *, tenant_id, query, auth_token, top_k=5, snapshot_ver=None):
            raise GroundingDegraded(403, "forbidden")

    deps = GraphDeps(llm=FakeLlm(), memory=DeniedMemory(),
                     case_reader=FakeCaseReader(), prompt_params={}, obo_token="t")
    outcome = await run_triage(deps, {"tenant_id": "t-1", "case_id": "c-91"})

    markers = [t for t in outcome.trace if t.get("event") == "grounding_degraded"]
    assert markers == [{"event": "grounding_degraded", "source": "memory-service",
                        "status": 403}]
    assert outcome.final_text  # the run still completed (ungrounded)
