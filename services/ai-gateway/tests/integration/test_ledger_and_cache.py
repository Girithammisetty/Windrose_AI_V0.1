"""Redis ledger atomicity + threshold exactly-once, AC-13 fallback chain,
pgvector semantic cache, outbox dispatcher."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.ledger import FallbackLedger, PgLedger, RedisLedger
from tests.conftest import (
    TENANT_A,
    TENANT_B,
    dp_headers,
    mint_key,
    seed_default_deployments,
)

pytestmark = pytest.mark.integration

KEY = "bud:itest-budget:2026-07-10"


async def test_redis_ledger_atomic_reservations(redis_client, clock):
    ledger = RedisLedger(redis_client, clock)
    results = await asyncio.gather(*[
        ledger.reserve(KEY, 100, 30, f"r{i}") for i in range(5)
    ])
    assert sum(results) == 3  # only 3×30 fit into 100
    spent, reserved = await ledger.usage(KEY)
    assert (spent, reserved) == (0, 90)


async def test_redis_ledger_settle_refunds_and_thresholds(redis_client, clock):
    ledger = RedisLedger(redis_client, clock)
    assert await ledger.reserve(KEY, 100, 50, "r1")
    prev, new = await ledger.settle(KEY, "r1", 20)
    assert (prev, new) == (0, 20)
    spent, reserved = await ledger.usage(KEY)
    assert (spent, reserved) == (20, 0)
    # SETNX threshold guard: exactly one winner under concurrency
    winners = await asyncio.gather(*[
        ledger.flag_once("budthr:itest:2026-07-10:95") for _ in range(8)
    ])
    assert sum(winners) == 1


async def test_redis_reservation_expiry(redis_client, clock):
    ledger = RedisLedger(redis_client, clock, reservation_ttl=180)
    assert await ledger.reserve(KEY, 100, 100, "r1")
    assert not await ledger.reserve(KEY, 100, 10, "r2")
    clock.advance(seconds=181)
    assert await ledger.reserve(KEY, 100, 10, "r2")  # expired reservation freed


async def test_pg_ledger_source_of_truth(engine, clock):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    ledger = PgLedger(session_factory, clock)
    assert await ledger.reserve(KEY, 100, 60, "r1")
    assert not await ledger.reserve(KEY, 100, 60, "r2")  # would overcommit
    prev, new = await ledger.settle(KEY, "r1", 45)
    assert (prev, new) == (0, 45)
    spent, reserved = await ledger.usage(KEY)
    assert (spent, reserved) == (45, 0)
    assert await ledger.flag_once("budthr:pg:95")
    assert not await ledger.flag_once("budthr:pg:95")


async def test_ac13_redis_down_falls_back_to_postgres(engine, clock, container,
                                                      client):
    """Budget checks keep succeeding on the Postgres path when Redis is dead,
    and the fallback alert fires."""
    import redis.asyncio as aioredis

    dead_redis = aioredis.from_url("redis://127.0.0.1:1/0",
                                   socket_connect_timeout=0.2)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    alerts = []
    container.gateway.budgets.ledger = FallbackLedger(
        RedisLedger(dead_redis, clock), PgLedger(session_factory, clock),
        on_fallback=lambda: alerts.append(1),
    )
    container.budget_engine.ledger = container.gateway.budgets.ledger

    await seed_default_deployments(container)
    _, secret = await mint_key(container, TENANT_A)
    from tests.conftest import CHAT_BODY

    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 200, r.text
    assert alerts  # degraded-path alert fired
    await dead_redis.aclose()


async def test_ac13_postgres_also_down_fails_closed(engine, clock, container,
                                                    client):
    import redis.asyncio as aioredis

    dead_redis = aioredis.from_url("redis://127.0.0.1:1/0",
                                   socket_connect_timeout=0.2)
    dead_engine = create_async_engine(
        "postgresql+asyncpg://x:x@127.0.0.1:1/x",
        connect_args={"timeout": 0.5},
    )
    dead_sessions = async_sessionmaker(dead_engine, expire_on_commit=False)
    container.gateway.budgets.ledger = FallbackLedger(
        RedisLedger(dead_redis, clock), PgLedger(dead_sessions, clock),
    )
    await seed_default_deployments(container)
    _, secret = await mint_key(container, TENANT_A)
    from tests.conftest import CHAT_BODY

    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    assert r.status_code == 503  # fail-closed proven (BR-14)
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    await dead_redis.aclose()
    await dead_engine.dispose()


CACHE_BODY = {
    "model": "windrose-auto",
    "temperature": 0.0,
    "messages": [{"role": "user", "content": (
        "aggregate the quarterly revenue by sales region including growth "
        "percentages and totals for every product line sold in northern and "
        "southern markets during the last fiscal year with monthly "
        "granularity and currency normalization applied " * 3)}],
}


async def test_pgvector_semantic_cache_hit_and_isolation(client, container):
    await seed_default_deployments(container)
    _, secret_a = await mint_key(container, TENANT_A)
    _, secret_b = await mint_key(container, TENANT_B, principal_id="ub")

    r1 = await client.post("/v1/chat/completions", json=CACHE_BODY,
                           headers=dp_headers(secret_a, TENANT_A))
    assert r1.status_code == 200, r1.text
    assert r1.headers["x-windrose-cache"] == "miss"

    similar = {**CACHE_BODY, "messages": [{
        "role": "user",
        "content": CACHE_BODY["messages"][0]["content"] + " thanks",
    }]}
    r2 = await client.post("/v1/chat/completions", json=similar,
                           headers=dp_headers(secret_a, TENANT_A))
    assert r2.headers["x-windrose-cache"] == "hit"

    # tenant B, identical prompt → miss (RLS isolates the semantic tier)
    r3 = await client.post("/v1/chat/completions", json=CACHE_BODY,
                           headers=dp_headers(secret_b, TENANT_B))
    assert r3.headers["x-windrose-cache"] == "miss"


async def test_outbox_dispatcher_publishes_usage_events(client, container):
    await seed_default_deployments(container)
    _, secret = await mint_key(container, TENANT_A)
    from tests.conftest import CHAT_BODY

    r = await client.post("/v1/chat/completions", json=CHAT_BODY,
                          headers=dp_headers(secret))
    request_id = r.headers["x-windrose-request-id"]
    assert container.bus.on_topic("ai.token_usage.v1") == []  # not yet dispatched
    published = await container.outbox_dispatcher.run_once()
    assert published >= 1
    events = container.bus.on_topic("ai.token_usage.v1")
    assert any(e["payload"]["request_id"] == request_id for e in events)
    # second poll publishes nothing (marked published)
    assert await container.outbox_dispatcher.run_once() == 0


async def test_redis_key_invalidation_across_replicas(redis_client, engine, clock,
                                                      container):
    """AIG-FR-031 via real Redis pub/sub: revocation reaches replica B."""
    from app.adapters.kv import RedisInvalidationChannel
    from app.domain.errors import KeyInvalid
    from app.domain.keys import KeyService

    chan_a = RedisInvalidationChannel(redis_client)
    chan_b = RedisInvalidationChannel(redis_client)
    await chan_a.start()
    await chan_b.start()
    replica_a = KeyService(container.uow_factory, clock, container.settings, chan_a)
    replica_b = KeyService(container.uow_factory, clock, container.settings, chan_b)

    key, secret = await replica_a.create(TENANT_A, principal_type="user",
                                         principal_id="u", max_rung=2,
                                         allowed_request_classes=None)
    assert (await replica_b.authenticate(secret)).id == key.id  # cached on B
    await replica_a.revoke(TENANT_A, key.id)
    await asyncio.sleep(0.3)  # pub/sub propagation (well under 30s)
    with pytest.raises(KeyInvalid):
        await replica_b.authenticate(secret)
    await chan_a.stop()
    await chan_b.stop()
