"""HttpQueryServiceClient (SEM-FR-024): forwards the caller's bearer JWT to
query-service's JWT-authenticated POST /api/v1/sql/dry-run (query-service has
no internal/SPIFFE route — this is the same pattern chart-service and
case-service use to call query-service) and maps its plan/ceiling response
onto {valid, estimated_bytes, verdict, message}."""

from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.query_client import HttpQueryServiceClient


def _client(handler) -> HttpQueryServiceClient:
    return HttpQueryServiceClient(transport=httpx.MockTransport(handler))


async def test_dry_run_forwards_bearer_token_and_binds():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {
            "estimated_scan_bytes": 2048, "ceiling_verdict": "ok",
        }})

    client = _client(handler)
    result = await client.dry_run(
        "tenant-a", "SELECT 1 WHERE x = $1",
        [{"type": "string", "value": "completed"}], "trino", "caller-jwt")

    assert captured["auth"] == "Bearer caller-jwt"
    assert captured["path"] == "/api/v1/sql/dry-run"
    assert captured["body"]["binds"] == ["completed"]
    assert result == {"valid": True, "estimated_bytes": 2048,
                      "verdict": "ok", "message": None}


async def test_dry_run_maps_cost_ceiling_exceeded_to_over_ceiling_verdict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": {
            "code": "COST_CEILING_EXCEEDED", "message": "estimated cost exceeds ceiling",
        }})

    client = _client(handler)
    result = await client.dry_run("tenant-a", "SELECT 1", [], "trino", "jwt")

    assert result["valid"] is True
    assert result["verdict"] == "over_ceiling"
    assert result["message"] == "estimated cost exceeds ceiling"


async def test_dry_run_maps_validation_failure_to_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": {
            "code": "VALIDATION_FAILED", "message": "not a SELECT",
        }})

    client = _client(handler)
    result = await client.dry_run("tenant-a", "DELETE FROM x", [], "trino", "jwt")

    assert result == {"valid": False, "estimated_bytes": None,
                      "verdict": "invalid", "message": "not a SELECT"}


async def test_dry_run_raises_on_unrecognized_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {
            "code": "UNAUTHENTICATED", "message": "invalid token",
        }})

    client = _client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.dry_run("tenant-a", "SELECT 1", [], "trino", "bad-jwt")
