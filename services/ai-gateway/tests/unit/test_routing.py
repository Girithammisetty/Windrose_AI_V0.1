"""Routing: AC-7 (failover), AC-10 (cloud affinity), circuit breaker, BR-8
drain fallback, health prober (AIG-FR-009a)."""

from __future__ import annotations

from tests.conftest import (
    CHAT_BODY,
    dp_headers,
    mint_key,
    seed_default_deployments,
    seed_deployment,
)


async def test_ac10_cloud_affinity_beats_priority(client, container):
    gcp = await seed_deployment(container, alias="fast-small", cloud="gcp",
                                priority=10, name="gcp-fast")
    await seed_deployment(container, alias="fast-small", cloud="aws",
                          priority=1, name="aws-fast")
    _, secret = await mint_key(container)
    from tests.conftest import make_token

    token = make_token(cell_cloud="gcp")
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret, token=token))
    assert r.status_code == 200
    assert r.headers["x-windrose-deployment"] == gcp.id
    span = container.tracer.spans_named("chat")[-1]
    assert span.attributes.get("windrose.routing.cross_cloud") in (None, False)


async def test_cross_cloud_only_when_no_same_cloud(client, container):
    aws = await seed_deployment(container, alias="fast-small", cloud="aws",
                                priority=1, name="aws-only")
    _, secret = await mint_key(container)
    from tests.conftest import make_token

    token = make_token(cell_cloud="gcp")
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret, token=token))
    assert r.status_code == 200
    assert r.headers["x-windrose-deployment"] == aws.id
    span = container.tracer.spans_named("chat")[-1]
    assert span.attributes["windrose.routing.cross_cloud"] is True


async def test_ac7_failover_after_two_500s(client, container):
    d1 = await seed_deployment(container, alias="fast-small", cloud="aws",
                               priority=1, name="primary")
    d2 = await seed_deployment(container, alias="fast-small", cloud="aws",
                               priority=2, name="secondary",
                               provider="anthropic")
    container.provider_client.script("primary", {"error": 500}, {"error": 500})
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200, r.text
    assert r.headers["x-windrose-deployment"] == d2.id
    span = container.tracer.spans_named("chat")[-1]
    attempts = span.attributes["windrose.routing.attempts"]
    assert [a["deployment"] for a in attempts] == [d1.id, d1.id, d2.id]
    assert attempts[-1]["outcome"] == "ok"


async def test_ac7_all_fail_returns_503(client, container):
    await seed_deployment(container, alias="fast-small", cloud="aws",
                          priority=1, name="p1")
    container.provider_client.script("p1", {"error": 500}, {"error": 500},
                                     {"error": 500})
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    span = container.tracer.spans_named("chat")[-1]
    assert span.attributes["windrose.rejected_stage"] == "provider"


async def test_timeout_is_retryable(client, container):
    await seed_deployment(container, alias="fast-small", cloud="aws",
                          priority=1, name="flaky")
    container.provider_client.script("flaky", {"timeout": True})
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200  # retry on same deployment succeeded


async def test_br8_draining_falls_through_to_next_rung_up(client, container):
    fast = await seed_deployment(container, alias="fast-small", cloud="aws",
                                 name="fast-1")
    balanced = await seed_deployment(container, alias="balanced", cloud="aws",
                                     name="bal-1")
    await container.provider_admin.drain(fast.id, force=True)
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200
    assert r.headers["x-windrose-deployment"] == balanced.id
    span = container.tracer.spans_named("chat")[-1]
    assert span.attributes["windrose.routing.rung_fallback"] == "up"


async def test_no_deployments_at_all_503(client, container):
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 503


async def test_circuit_breaker_opens_and_half_opens(container, clock):
    breaker = container.breaker
    for _ in range(5):
        breaker.record("dep-1", False)
    assert not breaker.allows("dep-1")
    assert breaker.state_of("dep-1") == "open"
    clock.advance(seconds=31)
    assert breaker.allows("dep-1")  # half-open probe
    assert breaker.state_of("dep-1") == "half_open"
    breaker.record("dep-1", True)
    assert breaker.state_of("dep-1") == "closed"


async def test_breaker_skips_deployment_in_routing(client, container):
    d1 = await seed_deployment(container, alias="fast-small", name="broken",
                               priority=1)
    d2 = await seed_deployment(container, alias="fast-small", name="healthy",
                               priority=2, provider="anthropic")
    for _ in range(5):
        container.breaker.record(d1.id, False)
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200
    assert r.headers["x-windrose-deployment"] == d2.id


async def test_health_prober_marks_unhealthy_and_recovers(container):
    d = await seed_deployment(container, alias="fast-small", name="probe-me")
    container.provider_client.script("probe-me", {"error": 500}, {"error": 500},
                                     {"error": 500})
    for _ in range(3):
        await container.prober.probe_once()
    assert not container.health.healthy(d.id)
    # next probe succeeds (script exhausted → echo) → recovery is automatic
    await container.prober.probe_once()
    assert container.health.healthy(d.id)


async def test_unhealthy_deployment_skipped_without_status_change(client, container):
    d1 = await seed_deployment(container, alias="fast-small", name="sick",
                               priority=1)
    d2 = await seed_deployment(container, alias="fast-small", name="well",
                               priority=2, provider="anthropic")
    for _ in range(3):
        container.health.record_probe(d1.id, False)
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.headers["x-windrose-deployment"] == d2.id
    refreshed = await container.provider_admin.get(d1.id)
    assert refreshed.status == "active"  # persisted status unchanged (FR-009a)


async def test_escalation_serves_next_rung(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    r1 = await client.post("/v1/chat/completions", json=CHAT_BODY,
                           headers=dp_headers(secret))
    assert r1.headers["x-windrose-rung"] == "0"
    r2 = await client.post(
        "/v1/chat/completions", json=CHAT_BODY,
        headers=dp_headers(secret, **{
            "x-windrose-escalate": "true",
            "x-windrose-prior-request-id": r1.headers["x-windrose-request-id"],
        }),
    )
    assert r2.status_code == 200
    assert r2.headers["x-windrose-rung"] == "1"
    span = container.tracer.spans_named("chat")[-1]
    assert span.attributes["windrose.escalated"] is True


async def test_ladder_cap_from_key(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container, max_rung=0)
    r = await client.post("/v1/chat/completions",
                          json={**CHAT_BODY, "model": "frontier"},
                          headers=dp_headers(secret))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "LADDER_CAP"


async def test_min_rung_header(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret, **{"x-windrose-min-rung": "1"}))
    assert r.status_code == 200
    assert r.headers["x-windrose-rung"] == "1"
