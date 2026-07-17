"""Streaming: AC-11 (SSE + usage chunk = metering), AC-15 (stream caps),
BR-2 (no mid-stream cutoff; pre-flight refusal), first-chunk failover."""

from __future__ import annotations

import json

from tests.conftest import (
    TENANT_A,
    dp_headers,
    ledger_key_for,
    mint_key,
    seed_default_deployments,
    seed_deployment,
)

STREAM_BODY = {
    "model": "windrose-auto",
    "stream": True,
    "stream_options": {"include_usage": True},
    "messages": [{"role": "user", "content": "stream me the numbers"}],
}


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line[6:]))
    return events


async def test_ac11_sse_chunks_and_usage_matches_metering(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=STREAM_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.strip().endswith("data: [DONE]")
    chunks = _parse_sse(r.text)
    deltas = [c for c in chunks if c["choices"][0]["delta"].get("content")]
    assert deltas, "expected content chunks"
    usage_chunks = [c for c in chunks if "usage" in c]
    assert len(usage_chunks) == 1  # usage chunk always forwarded (AIG-FR-010)
    usage = usage_chunks[0]["usage"]

    event = container.bus.on_topic("ai.token_usage.v1")[-1]
    assert event["payload"]["input_tokens"] == usage["prompt_tokens"]
    assert event["payload"]["output_tokens"] == usage["completion_tokens"]
    assert event["payload"]["first_token_ms"] is not None


async def test_stream_failover_before_first_byte(client, container):
    await seed_deployment(container, alias="fast-small", name="s1", priority=1)
    d2 = await seed_deployment(container, alias="fast-small", name="s2",
                               priority=2, provider="anthropic")
    container.provider_client.script("s1", {"stream_error": 503},
                                     {"stream_error": 503})
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=STREAM_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200
    assert "echo(s2" in r.text
    del d2


async def test_stream_no_retry_after_bytes_sent(client, container):
    """AIG-FR-008: once bytes streamed, a failure ends the stream (no replay)."""
    await seed_deployment(container, alias="fast-small", name="s1", priority=1)
    await seed_deployment(container, alias="fast-small", name="s2", priority=2,
                          provider="anthropic")

    async def broken_stream(deployment, request):
        from app.domain.ports import ProviderError

        yield {"delta": "partial"}
        raise ProviderError(500)

    original = container.provider_client.stream
    calls = {"n": 0}

    def stream(deployment, request):
        if calls["n"] == 0 and deployment.deployment_name == "s1":
            calls["n"] += 1
            return broken_stream(deployment, request)
        return original(deployment, request)

    container.provider_client.stream = stream
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=STREAM_BODY,
                          headers=dp_headers(secret))
    assert "partial" in r.text
    assert "UPSTREAM_UNAVAILABLE" in r.text  # error chunk, not a silent replay
    assert "echo(s2" not in r.text  # never re-streamed from another deployment


async def test_all_stream_attempts_fail_yields_error_and_releases(client, container):
    await seed_deployment(container, alias="fast-small", name="s1", priority=1)
    container.provider_client.script("s1", {"stream_error": 503},
                                     {"stream_error": 503}, {"stream_error": 503})
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=STREAM_BODY,
                          headers=dp_headers(secret))
    assert "UPSTREAM_UNAVAILABLE" in r.text
    # stream slot released, budget refunded
    assert await container.kv.get(f"adm:{TENANT_A}:streams") in (None, "0")
    key = ledger_key_for(f"default-{TENANT_A}-daily", "daily", container.clock)
    spent, reserved = await container.ledger.usage(key)
    assert spent == 0 and reserved == 0


async def test_br2_stream_refused_preflight_when_exhausted(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    key = ledger_key_for(f"default-{TENANT_A}-daily", "daily", container.clock)
    await container.ledger.settle(key, "seed", 1_000_000)
    r = await client.post("/v1/chat/completions", json=STREAM_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 402  # refused pre-flight, no hung stream (US-5)
    assert await container.kv.get(f"adm:{TENANT_A}:streams") in (None, "0")


async def test_stream_settles_budget_post_stream(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    await client.post("/v1/chat/completions", json=STREAM_BODY,
                      headers=dp_headers(secret))
    key = ledger_key_for(f"default-{TENANT_A}-daily", "daily", container.clock)
    spent, reserved = await container.ledger.usage(key)
    assert spent >= 1
    assert reserved == 0


async def test_cached_stream_replayed_as_sse(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    body = {**STREAM_BODY, "temperature": 0.0}
    await client.post("/v1/chat/completions", json=body, headers=dp_headers(secret))
    r = await client.post("/v1/chat/completions", json=body,
                          headers=dp_headers(secret))
    assert r.headers["x-windrose-cache"] == "hit"
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.strip().endswith("data: [DONE]")
    event = container.bus.on_topic("ai.token_usage.v1")[-1]
    assert event["payload"]["cached"] is True
