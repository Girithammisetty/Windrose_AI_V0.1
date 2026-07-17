"""Admission control (AIG-FR-011, BR-13, AC-15)."""

from __future__ import annotations

from tests.conftest import (
    TENANT_A,
    dp_headers,
    make_settings,
    mint_key,
    seed_default_deployments,
)

STREAM_BODY = {
    "model": "windrose-auto",
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
}


async def _capped_app(clock, **overrides):
    import httpx

    from app.container import build_container
    from app.main import create_app
    from tests.conftest import _noop_sleeper

    settings = make_settings(**overrides)
    container = build_container(settings, mode="memory", clock=clock,
                                sleeper=_noop_sleeper)
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://t")
    return container, client


async def test_ac15_stream_cap_429_then_slot_frees(clock):
    container, client = await _capped_app(clock, streams_cap_per_tenant=1)
    async with client:
        await seed_default_deployments(container)
        _, secret = await mint_key(container)
        # occupy the single slot
        await container.admission.acquire_stream(TENANT_A)
        r = await client.post("/v1/chat/completions", json=STREAM_BODY,
                              headers=dp_headers(secret))
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "RATE_LIMITED"
        assert int(r.headers["retry-after"]) >= 1
        assert container.provider_client.calls == []  # before any provider call
        # a completing request frees the slot → next stream admitted
        await container.admission.release_stream(TENANT_A)
        r2 = await client.post("/v1/chat/completions", json=STREAM_BODY,
                               headers=dp_headers(secret))
        assert r2.status_code == 200
        # ... and the finished stream released its own slot again
        r3 = await client.post("/v1/chat/completions", json=STREAM_BODY,
                               headers=dp_headers(secret))
        assert r3.status_code == 200


async def test_rpm_cap(clock):
    container, client = await _capped_app(clock, rpm_cap_per_tenant=2)
    async with client:
        await seed_default_deployments(container)
        _, secret = await mint_key(container)
        body = {"model": "windrose-auto",
                "messages": [{"role": "user", "content": "x"}]}
        for _ in range(2):
            assert (await client.post("/v1/chat/completions", json=body,
                                      headers=dp_headers(secret))).status_code == 200
        r = await client.post("/v1/chat/completions", json=body,
                              headers=dp_headers(secret))
        assert r.status_code == 429
        assert "retry-after" in r.headers
        # window rolls over → admitted again (BR-13)
        clock.advance(seconds=61)
        r = await client.post("/v1/chat/completions", json=body,
                              headers=dp_headers(secret))
        assert r.status_code == 200


async def test_tpm_cap(clock):
    container, client = await _capped_app(clock, tpm_cap_per_tenant=10)
    async with client:
        await seed_default_deployments(container)
        _, secret = await mint_key(container)
        body = {"model": "windrose-auto",
                "messages": [{"role": "user", "content": "w" * 400}]}
        r = await client.post("/v1/chat/completions", json=body,
                              headers=dp_headers(secret))
        assert r.status_code == 429
        span = container.tracer.spans_named("chat")[-1]
        assert span.attributes["windrose.rejected_stage"] == "admission"
