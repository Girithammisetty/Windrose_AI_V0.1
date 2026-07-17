"""Semantic cache: AC-6 (isolation + $0 metering), semantic tier, eligibility
rules (AIG-FR-042), invalidation (AIG-FR-043), BR-6/15."""

from __future__ import annotations

from tests.conftest import (
    TENANT_A,
    TENANT_B,
    dp_headers,
    mint_key,
    seed_default_deployments,
)

CACHEABLE_BODY = {
    "model": "windrose-auto",
    "temperature": 0.0,
    "messages": [{"role": "user", "content": "revenue by region for Q3"}],
}


async def test_ac6_exact_hit_same_tenant_miss_other_tenant(client, container):
    await seed_default_deployments(container)
    _, secret_a = await mint_key(container, TENANT_A)
    _, secret_b = await mint_key(container, TENANT_B, principal_id="user-b")

    r1 = await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                           headers=dp_headers(secret_a, TENANT_A))
    assert r1.headers["x-windrose-cache"] == "miss"

    # tenant B, identical prompt → MISS (isolation)
    r2 = await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                           headers=dp_headers(secret_b, TENANT_B))
    assert r2.headers["x-windrose-cache"] == "miss"

    # tenant A repeat → HIT with $0 metering
    r3 = await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                           headers=dp_headers(secret_a, TENANT_A))
    assert r3.headers["x-windrose-cache"] == "hit"
    assert (r3.json()["choices"][0]["message"]["content"]
            == r1.json()["choices"][0]["message"]["content"])
    event = container.bus.on_topic("ai.token_usage.v1")[-1]
    assert event["payload"]["cached"] is True
    assert event["payload"]["cost_usd"] == 0
    span = container.tracer.spans_named("chat")[-1]
    assert span.attributes["windrose.cache"] == "hit_exact"
    # only two provider calls happened in total
    assert len(container.provider_client.calls) == 2


async def test_semantic_tier_hit_on_similar_long_prompt(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    words = ("show the quarterly revenue aggregated by sales region including "
             "growth percentages and totals for every product line we sell "
             "in the northern and southern markets during the last fiscal "
             "year with monthly granularity and currency normalization "
             "applied to euros " * 3)
    body1 = {**CACHEABLE_BODY,
             "messages": [{"role": "user", "content": words + "please"}]}
    body2 = {**CACHEABLE_BODY,
             "messages": [{"role": "user", "content": words + "kindly"}]}
    r1 = await client.post("/v1/chat/completions", json=body1,
                           headers=dp_headers(secret))
    assert r1.headers["x-windrose-cache"] == "miss"
    r2 = await client.post("/v1/chat/completions", json=body2,
                           headers=dp_headers(secret))
    assert r2.headers["x-windrose-cache"] == "hit", r2.headers
    span = container.tracer.spans_named("chat")[-1]
    assert span.attributes["windrose.cache"] == "hit_semantic"


async def test_br15_short_prompts_skip_semantic_tier(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    b1 = {**CACHEABLE_BODY,
          "messages": [{"role": "user", "content": "tiny prompt one"}]}
    b2 = {**CACHEABLE_BODY,
          "messages": [{"role": "user", "content": "tiny prompt two"}]}
    await client.post("/v1/chat/completions", json=b1, headers=dp_headers(secret))
    r = await client.post("/v1/chat/completions", json=b2,
                          headers=dp_headers(secret))
    assert r.headers["x-windrose-cache"] == "miss"


async def test_judge_and_high_temperature_never_cached(client, container):
    await seed_default_deployments(container)
    _, secret_judge = await mint_key(container, classes=["judge"])
    r = await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                          headers=dp_headers(secret_judge, request_class="judge"))
    assert r.headers["x-windrose-cache"] == "skip"

    _, secret = await mint_key(container, principal_id="u2")
    hot = {**CACHEABLE_BODY, "temperature": 0.9}
    r = await client.post("/v1/chat/completions", json=hot,
                          headers=dp_headers(secret))
    assert r.headers["x-windrose-cache"] == "skip"


async def test_ttl_expiry(client, container, clock):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                      headers=dp_headers(secret))
    clock.advance(seconds=86_401)  # default TTL 24h
    r = await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                          headers=dp_headers(secret))
    assert r.headers["x-windrose-cache"] == "miss"


async def test_br6_guardrail_flagged_responses_not_cached(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    body = {**CACHEABLE_BODY,
            "messages": [{"role": "user",
                          "content": "send to a@b.co the report " * 40}]}
    r1 = await client.post("/v1/chat/completions", json=body,
                           headers=dp_headers(secret))
    assert "pii_redacted" in r1.headers.get("x-windrose-guardrail-flags", "")
    r2 = await client.post("/v1/chat/completions", json=body,
                           headers=dp_headers(secret))
    assert r2.headers["x-windrose-cache"] == "miss"  # never cached (BR-6)


async def test_admin_cache_invalidation(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                      headers=dp_headers(secret))
    from tests.conftest import admin_auth

    r = await client.delete("/api/v1/admin/cache?scope=tenant",
                            headers=admin_auth())
    assert r.status_code == 200
    r2 = await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                           headers=dp_headers(secret))
    assert r2.headers["x-windrose-cache"] == "miss"


async def test_guardrail_policy_change_invalidates_cache(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                      headers=dp_headers(secret))
    from tests.conftest import admin_auth

    r = await client.put("/api/v1/admin/guardrails", json={"policy": {
        "pii": {"mode": "redact", "entities": ["EMAIL"], "deredact_response": False},
        "injection": {"mode": "block", "flag_threshold": 0.65,
                      "block_threshold": 0.85},
        "schema_validation": "on",
    }}, headers=admin_auth())
    assert r.status_code == 200
    # context_hash changed (policy version) + exact tier flushed → miss
    r2 = await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                           headers=dp_headers(secret))
    assert r2.headers["x-windrose-cache"] == "miss"


async def test_tenant_ttl_zero_disables_cache(client, container):
    from app.domain.entities import TenantConfig

    await seed_default_deployments(container)
    async with container.uow_factory(TENANT_A) as uow:
        await uow.tenant_configs.put(TenantConfig(tenant_id=TENANT_A,
                                                  cache_ttl_seconds=0))
        await uow.commit()
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CACHEABLE_BODY,
                          headers=dp_headers(secret))
    assert r.headers["x-windrose-cache"] == "skip"
