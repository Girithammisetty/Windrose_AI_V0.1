"""Data-plane basics: AC-1, key auth (AC-9), attribution validation,
embeddings, legacy completions."""

from __future__ import annotations

from app.domain.keys import KeyService
from tests.conftest import (
    CHAT_BODY,
    TENANT_A,
    TENANT_B,
    dp_headers,
    make_token,
    mint_key,
    seed_default_deployments,
)


async def test_ac1_chat_success_headers_and_metering(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200, r.text
    assert r.headers["x-trace-id"]
    assert r.headers["x-windrose-rung"] == "0"
    assert r.headers["x-windrose-deployment"]  # AIG-FR-009b
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["usage"]["total_tokens"] > 0

    request_id = r.headers["x-windrose-request-id"]
    usage_events = container.bus.on_topic("ai.token_usage.v1")
    assert len(usage_events) == 1
    payload = usage_events[0]["payload"]
    assert payload["request_id"] == request_id
    assert payload["input_tokens"] == body["usage"]["prompt_tokens"]
    assert payload["output_tokens"] == body["usage"]["completion_tokens"]
    assert payload["cost_usd"] > 0
    assert payload["price_version"] == container.settings.price_version


async def test_span_attribute_contract(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    await client.post("/v1/chat/completions", json=CHAT_BODY,
                      headers=dp_headers(secret))
    span = container.tracer.spans_named("chat")[-1]
    for attr in ("windrose.tenant_id", "windrose.request_class", "windrose.rung",
                 "windrose.deployment", "windrose.cache", "windrose.budget_state",
                 "windrose.price_version", "gen_ai.usage.input_tokens",
                 "gen_ai.usage.output_tokens", "gen_ai.request.model"):
        assert attr in span.attributes, attr


async def test_missing_key_is_401_key_invalid(client, container):
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers={"X-Windrose-JWT": make_token()})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "KEY_INVALID"


async def test_missing_jwt_is_401(client, container):
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_key_from_other_tenant_is_key_invalid(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container, TENANT_B)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret, TENANT_A))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "KEY_INVALID"


async def test_ac9_revocation_effective_across_replicas(container, clock):
    """Revocation through replica A's service is seen by replica B ≤ 30s."""
    key, secret = await mint_key(container)
    replica_b = KeyService(container.uow_factory, clock, container.settings,
                           container.invalidation)
    assert (await replica_b.authenticate(secret)).id == key.id  # warm B's cache
    await container.key_service.revoke(TENANT_A, key.id)
    clock.advance(seconds=1)  # well under 30s
    import pytest

    from app.domain.errors import KeyInvalid

    with pytest.raises(KeyInvalid):
        await replica_b.authenticate(secret)


async def test_expired_key_is_rejected(container, clock):
    import pytest

    from app.domain.errors import KeyInvalid

    key, secret = await mint_key(container)
    key.expires_at = clock.now()
    async with container.uow_factory(TENANT_A) as uow:
        await uow.keys.update(key)
        await uow.commit()
    await container.invalidation.publish("key", key.id)
    with pytest.raises(KeyInvalid):
        await container.key_service.authenticate(secret)


async def test_request_class_not_allowed_by_key(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container, classes=["embed"])
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret, request_class="chat"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_agent_attribution_mismatch_rejected(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    token = make_token(typ="agent_obo", agent_id="analytics", agent_version="14",
                       obo_sub="user-1")
    r = await client.post(
        "/v1/chat/completions", json=CHAT_BODY,
        headers=dp_headers(secret, token=token,
                           **{"x-windrose-agent-id": "other-agent"}),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_tenant_header_is_ignored_for_identity(client, container):
    """AIG-FR-002: x-windrose-tenant-id must be ignored; JWT wins."""
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    r = await client.post(
        "/v1/chat/completions", json=CHAT_BODY,
        headers=dp_headers(secret, **{"x-windrose-tenant-id": TENANT_B}),
    )
    assert r.status_code == 200
    event = container.bus.on_topic("ai.token_usage.v1")[-1]
    assert event["tenant_id"] == TENANT_A


async def test_embeddings_endpoint(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    r = await client.post("/v1/embeddings",
                          json={"model": "windrose-auto", "input": ["a", "b"]},
                          headers=dp_headers(secret))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["data"]) == 2
    assert body["usage"]["prompt_tokens"] > 0
    event = container.bus.on_topic("ai.token_usage.v1")[-1]
    assert event["payload"]["request_class"] == "embed"


async def test_embeddings_batch_cap(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    r = await client.post("/v1/embeddings",
                          json={"input": ["x"] * 257},
                          headers=dp_headers(secret))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_legacy_completions(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container)
    r = await client.post("/v1/completions",
                          json={"model": "windrose-auto", "prompt": "hello"},
                          headers=dp_headers(secret))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "text_completion"
    assert "hello" in body["choices"][0]["text"]


async def test_unknown_request_class_rejected(client, container):
    _, secret = await mint_key(container)
    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret, request_class="mystery"))
    assert r.status_code == 422
