"""End-to-end integration against the REAL local infra (no stubs in the path):

* Ollama (OpenAI-compatible API at http://localhost:11434/v1) — real chat,
  streaming and embedding inference.
* Redpanda / Kafka (localhost:9092) — real ``ai.token_usage.v1`` metering.
* Redis (localhost:6379) — real budget ledger (reserve → settle) + exact cache.
* OPA sidecar (localhost:8281) — real authorization decisions.

These exercise the full enforcement pipeline with the real ``OllamaProvider``
wired as the provider port, the real ``KafkaEventBus`` as the outbox sink, and a
real ``RedisLedger``/``RedisKV`` — the ``InProcessProvider`` and in-memory
doubles are never in the path. Each fixture auto-skips with a clear message when
its dependency is unreachable, per CONVENTIONS.md.

Run: bring up deploy/docker-compose.dev.yml + Ollama, then ``make test`` or
``uv run pytest tests/integration/test_real_llm.py -q -m integration -s``.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.adapters.kv import RedisKV
from app.adapters.ledger import RedisLedger
from app.adapters.providers import OllamaProvider, OpenAICompatibleProvider
from app.adapters.registry import ProviderRegistry, resolve_credential
from app.container import build_container
from app.domain.entities import ProviderDeployment
from app.domain.ports import ProviderRequest
from app.domain.pricing import PriceTable
from app.events.bus import KafkaEventBus
from app.main import create_app
from tests.conftest import (
    TENANT_A,
    FakeClock,
    dp_headers,
    ledger_key_for,
    make_settings,
    mint_key,
)

pytestmark = pytest.mark.integration

OLLAMA_BASE = "http://localhost:11434/v1"
OLLAMA_TAGS = "http://localhost:11434/api/tags"
KAFKA = "localhost:9092"
OPA_URL = "http://localhost:8281"
# Dedicated logical DB for this suite: the redis_client fixture FLUSHES its
# database, and db 0 is the SHARED dev projection store (rbac perm:* +
# authz:proj:* authorization keys). Flushing db 0 wipes live authorization
# state for the whole running stack — keep this tier isolated in db 8.
REDIS_URL = "redis://localhost:6379/8"
USAGE_TOPIC = "ai.token_usage.v1"

CHAT_MODEL = "qwen2.5:0.5b"
EMBED_MODEL = "nomic-embed-text"


# --------------------------------------------------------------------- reachability


def _reachable(url: str) -> bool:
    try:
        with httpx.Client(timeout=3.0) as c:
            c.get(url)
        return True
    except Exception:  # noqa: BLE001
        return False


def _ollama_models() -> set[str]:
    with httpx.Client(timeout=3.0) as c:
        tags = c.get(OLLAMA_TAGS).json()
    return {m["name"] for m in tags.get("models", [])}


@pytest.fixture(scope="module")
def _require_ollama():
    if not _reachable(OLLAMA_TAGS):
        pytest.skip("Ollama unreachable at localhost:11434 — skipping real-LLM tier")
    if not any(n.startswith(CHAT_MODEL) for n in _ollama_models()):
        pytest.skip(f"Ollama model {CHAT_MODEL} not pulled — run: ollama pull {CHAT_MODEL}")


@pytest.fixture(scope="module")
def _require_embed(_require_ollama):
    if not any(n.startswith(EMBED_MODEL) for n in _ollama_models()):
        pytest.skip(f"Ollama model {EMBED_MODEL} not pulled — run: ollama pull {EMBED_MODEL}")


@pytest.fixture
async def redis_client():
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(REDIS_URL)
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis unreachable at localhost:6379: {exc}")
        return
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def kafka_bus():
    bus = KafkaEventBus(KAFKA)
    try:
        # force a lazy producer start to verify Redpanda is reachable
        await bus._client.start()  # noqa: SLF001
        bus._started = True  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Kafka/Redpanda unreachable at localhost:9092: {exc}")
        return
    yield bus
    await bus.aclose()


@pytest.fixture
def clock():
    return FakeClock()


async def _noop_sleeper(ms: int) -> None:
    return None


@pytest.fixture
async def container(_require_ollama, redis_client, kafka_bus, clock):
    """Memory-mode container with every provider/sink swapped for a REAL adapter:
    real Ollama LLM, real Redis ledger + KV, real Kafka bus. The outbox publishes
    to real Redpanda on commit."""
    settings = make_settings()
    provider = OllamaProvider(OLLAMA_BASE, timeout_s=120.0)
    ledger = RedisLedger(redis_client, clock, settings.reservation_ttl_seconds)
    kv = RedisKV(redis_client)
    c = build_container(
        settings, mode="memory", clock=clock, sleeper=_noop_sleeper,
        provider_client=provider, bus=kafka_bus, ledger=ledger, kv=kv,
    )
    yield c
    await provider.aclose()


@pytest.fixture
async def client(container):
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_ollama_deployments(container):
    await container.provider_admin.create({
        "provider": "ollama", "model_family": "fast-small",
        "deployment_name": CHAT_MODEL, "region": "local", "cloud": "aws",
        "endpoint_vault_ref": "local/ollama/chat", "priority": 1,
    })
    await container.provider_admin.create({
        "provider": "ollama", "model_family": "embed-standard",
        "deployment_name": EMBED_MODEL, "region": "local", "cloud": "aws",
        "endpoint_vault_ref": "local/ollama/embed", "priority": 1,
    })


async def _consume_usage_event(request_id: str, timeout_s: float = 15.0) -> dict | None:
    """Read ai.token_usage.v1 from real Redpanda and return the envelope whose
    payload.request_id matches (earliest offset, filtered)."""
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        USAGE_TOPIC, bootstrap_servers=KAFKA,
        group_id=f"aig-itest-{uuid.uuid4()}", enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        import time
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            batch = await consumer.getmany(timeout_ms=1000, max_records=200)
            for _tp, messages in batch.items():
                for msg in messages:
                    env = json.loads(msg.value)
                    if (env.get("payload") or {}).get("request_id") == request_id:
                        return env
        return None
    finally:
        await consumer.stop()


# --------------------------------------------------------------------------- tests


async def test_real_chat_completion_full_pipeline_and_kafka_metering(
    client, container, clock
):
    """(a) A real /v1/chat/completions call returns actual qwen2.5 output through
    the full pipeline; budget is reserved→settled on the real Redis ledger and a
    metering event is published to real Redpanda."""
    await _seed_ollama_deployments(container)
    _, secret = await mint_key(container)

    body = {
        "model": "windrose-auto",
        "messages": [{"role": "user", "content": "Reply with a short greeting."}],
        "max_tokens": 64,
    }
    r = await client.post("/v1/chat/completions", json=body, headers=dp_headers(secret))
    assert r.status_code == 200, r.text
    out = r.json()
    content = out["choices"][0]["message"]["content"]
    assert content.strip(), "expected real model output, got empty content"
    assert out["usage"]["completion_tokens"] > 0
    assert out["usage"]["prompt_tokens"] > 0
    assert r.headers["x-windrose-deployment"]

    # real Redis ledger: reservation (taken at the alias price) is released and
    # the request settles on the ledger. Local Ollama is priced at $0/$0 (the
    # seeded per-(provider, model) cost detail), so an ollama-served request
    # settles zero spend — proving cost is attributed by the ACTUAL provider, not
    # the ladder rung alias.
    daily_key = ledger_key_for(f"default-{TENANT_A}-daily", "daily", clock)
    spent, reserved = await container.ledger.usage(daily_key)
    assert spent == 0, "ollama is $0/$0; an ollama-served request settles no spend"
    assert reserved == 0, "reservation should be released after settle"

    # real Redpanda: the metering envelope landed on ai.token_usage.v1, carrying
    # the cost-detail attribution (provider + concrete model + price provenance).
    request_id = r.headers["x-windrose-request-id"]
    env = await _consume_usage_event(request_id)
    assert env is not None, "no ai.token_usage.v1 event found on real Redpanda"
    assert env["tenant_id"] == TENANT_A
    payload = env["payload"]
    assert payload["output_tokens"] == out["usage"]["completion_tokens"]
    assert payload["provider"] == "ollama"
    assert payload["model"] == CHAT_MODEL  # concrete provider-side model id
    assert payload["cost_usd"] == 0  # local inference is free
    assert payload["price_source"] == "provider_zero"

    print(f"\n[REAL qwen2.5 output] {content!r}")
    print(f"[usage] prompt={out['usage']['prompt_tokens']} "
          f"completion={out['usage']['completion_tokens']}")


async def test_registry_second_provider_openai_path_against_real_ollama(_require_ollama):
    """Provider-agnostic dispatch, LIVE: an `openai`-typed deployment pointed at
    the local Ollama OpenAI-compatible endpoint routes through the
    OpenAICompatibleProvider — a DIFFERENT code path from the `ollama`-typed
    deployment — but still produces real tokens against real infra, and cost is
    attributed to provider+model with a DIFFERENT price than the ollama-typed
    deployment on the SAME model/infra."""
    settings = make_settings(ollama_base_url=OLLAMA_BASE)
    registry = ProviderRegistry(settings)

    def dep(provider: str) -> ProviderDeployment:
        return ProviderDeployment(
            id=str(uuid.uuid4()), tenant_id=TENANT_A, provider=provider,
            model_family="fast-small", deployment_name=CHAT_MODEL, region="local",
            cloud="aws", endpoint_vault_ref=OLLAMA_BASE, tpm_limit=0, rpm_limit=0,
            priority=1,
        )

    ollama_dep, openai_dep = dep("ollama"), dep("openai")

    # Distinct adapter instances / code paths for the two provider types.
    ollama_adapter = registry._adapter_for(ollama_dep)
    openai_adapter = registry._adapter_for(openai_dep)
    assert isinstance(openai_adapter, OpenAICompatibleProvider)
    assert openai_adapter._label == "openai" and ollama_adapter._label == "ollama"
    assert openai_adapter is not ollama_adapter

    # openai-typed credential resolves the URL ref as the base_url (gateway store).
    cred = resolve_credential(openai_dep, settings)
    assert cred.base_url == OLLAMA_BASE

    # Real round trip through the OpenAI-compatible adapter against real Ollama.
    req = ProviderRequest(
        model=CHAT_MODEL, messages=[{"role": "user", "content": "Say hi."}],
        max_tokens=32, temperature=0.0)
    result = await registry.complete(openai_dep, req)
    assert result.content.strip(), "expected real model output via openai path"
    assert result.input_tokens > 0 and result.output_tokens > 0

    # Cost attribution differs by provider for the SAME model on the SAME infra:
    # ollama-typed is free ($0), openai-typed falls back to the alias price (>0).
    prices = PriceTable(version="test")
    q_ollama = prices.quote_for("ollama", CHAT_MODEL, "fast-small")
    q_openai = prices.quote_for("openai", CHAT_MODEL, "fast-small")
    assert q_ollama.cost_cents(result.input_tokens, result.output_tokens) == 0
    assert q_openai.cost_cents(result.input_tokens, result.output_tokens) > 0
    assert q_openai.source == "alias" and q_ollama.source == "provider_zero"

    await registry.aclose()
    print(f"\n[REAL openai-compat via Ollama] {result.content!r}")


async def test_real_embeddings_returns_nomic_vector(_require_embed, client, container):
    """(b) A real /v1/embeddings call returns a genuine nomic-embed-text vector."""
    await _seed_ollama_deployments(container)
    _, secret = await mint_key(container)

    r = await client.post(
        "/v1/embeddings",
        json={"model": "windrose-auto", "input": ["revenue by region for Q3"]},
        headers=dp_headers(secret),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    vec = body["data"][0]["embedding"]
    assert len(vec) >= 256, f"expected a real embedding vector, got dim {len(vec)}"
    assert any(abs(x) > 1e-6 for x in vec), "vector is all zeros — not a real model output"
    assert body["usage"]["prompt_tokens"] > 0
    norm = sum(x * x for x in vec) ** 0.5
    assert norm > 0
    print(f"\n[REAL nomic-embed-text] dim={len(vec)} first3={vec[:3]} l2norm={norm:.4f}")


async def test_real_streaming_yields_real_tokens(client, container):
    """(d) SSE streaming yields real Ollama tokens (multiple content deltas)."""
    await _seed_ollama_deployments(container)
    _, secret = await mint_key(container)

    body = {
        "model": "windrose-auto",
        "messages": [{"role": "user", "content": "Count from one to five."}],
        "max_tokens": 64,
        "stream": True,
    }
    deltas: list[str] = []
    saw_done = False
    saw_usage = False
    async with client.stream("POST", "/v1/chat/completions", json=body,
                             headers=dp_headers(secret)) as resp:
        assert resp.status_code == 200, await resp.aread()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                saw_done = True
                continue
            chunk = json.loads(data)
            if chunk.get("usage"):
                saw_usage = True
            for ch in chunk.get("choices", []):
                piece = (ch.get("delta") or {}).get("content")
                if piece:
                    deltas.append(piece)

    text = "".join(deltas)
    assert len(deltas) >= 1 and text.strip(), "no real streamed tokens received"
    assert saw_usage, "streaming did not emit the final usage chunk (AIG-FR-010)"
    assert saw_done
    print(f"\n[REAL streamed tokens] {len(deltas)} deltas -> {text!r}")


async def test_real_opa_authorization_decision(redis_client):
    """(c) Authorization decisions come from the real OPA container: an
    allow (tenant-scoped action in the projection) and a deny (unknown action)."""
    if not _reachable(f"{OPA_URL}/health"):
        pytest.skip("OPA unreachable at localhost:8281 — skipping OPA test")
    from windrose_common.opaclient import OpaClient

    opa = OpaClient(OPA_URL)  # posts input.projection to windrose/authz_input

    allow_projection = {
        "action_known": True,
        "action_scoped": False,
        "autonomous_enabled": False,
        "flags": {"found": False, "admin": False, "ws_admin": []},
        "tenant_actions": {"found": True, "actions": ["ai.invoke"]},
        "workspace": {"assigned": False, "actions": [], "archived": False},
        "resource": {"found": False, "level": "", "archived": False},
        "workspace_archived_tenant": False,
    }
    allow = await opa.decision(
        subject={"id": "user-1", "typ": "user", "scopes": ["ai.invoke"]},
        action="ai.invoke", tenant=TENANT_A, projection=allow_projection,
    )
    assert allow.allow is True, f"expected real OPA allow, got {allow}"
    assert allow.reason == "allowed"

    deny = await opa.decision(
        subject={"id": "user-1", "typ": "user", "scopes": []},
        action="ai.invoke", tenant=TENANT_A,
        projection={**allow_projection, "action_known": False},
    )
    assert deny.allow is False
    print(f"\n[REAL OPA] allow={allow.allow}/{allow.reason}  deny={deny.allow}/{deny.reason}")
