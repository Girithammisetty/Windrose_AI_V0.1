"""Integration: semantic-service's REAL runtime adapters against the live dev
infra (Ollama embeddings, Redpanda/Kafka bus + outbox, OPA + Redis, pgvector).
Proves the stub-removal wiring speaks the real wire protocol. Each test
auto-skips when its endpoint is unreachable (CONVENTIONS.md testing tier 2).
"""

from __future__ import annotations

import socket
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adapters.embeddings import LocalHashEmbedding, OpenAIEmbeddingClient
from app.adapters.query_client import FakeQueryServiceClient
from app.container import build_container
from app.domain.services import CallCtx
from tests.conftest import SALES_DEFINITION, TENANT_A, WORKSPACE, make_settings

pytestmark = pytest.mark.integration


def _reachable(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=1.0):
            return True
    except OSError:
        return False


def _require(port: int, name: str) -> None:
    if not _reachable(port):
        pytest.skip(f"{name} not reachable on localhost:{port} — dev infra down")


def _ctx(subject: str) -> CallCtx:
    return CallCtx(tenant_id=TENANT_A, actor={"type": "user", "id": subject},
                   subject=subject)


# --- (a) REAL nomic-embed-text embeddings + pgvector ANN search ---------------


async def test_ollama_embeddings_are_real_dense_768_vectors():
    """The real embedding adapter returns a dense 768-dim nomic-embed-text vector
    — not the sparse hash fake. Density (~all components non-zero) is the
    discriminator: LocalHashEmbedding yields a handful of non-zero buckets."""
    _require(11434, "Ollama")
    client = OpenAIEmbeddingClient()  # defaults to Ollama /v1/embeddings, nomic
    vec = await client.embed(TENANT_A, "monthly revenue by region for the last year")
    assert len(vec) == 768
    nonzero = sum(1 for x in vec if abs(x) > 1e-9)
    assert nonzero > 700, f"expected a dense real embedding, got {nonzero} non-zero"

    # The hash double, on the same short text, is sparse — proving they differ.
    hashed = await LocalHashEmbedding(768).embed(TENANT_A, "monthly revenue by region")
    assert sum(1 for x in hashed if abs(x) > 1e-9) < 50


async def test_verified_query_pgvector_ann_search_with_real_embeddings(engine, clock):
    """A verified query is embedded with a REAL nomic vector, stored in the
    pgvector column, and cosine-ANN search (`<=>`) over pgvector returns it —
    ranked above a semantically unrelated approved query. Hits: Ollama + pgvector.
    """
    _require(11434, "Ollama")
    settings = make_settings()  # embedding_dim=768 matches the vector(768) column
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    container = build_container(
        settings, mode="sql", session_factory=session_factory, clock=clock,
        embeddings=OpenAIEmbeddingClient(),  # REAL Ollama embeddings
    )
    svc = container.verified_query_service

    async def approved(nl: str, sql: str) -> str:
        vq = await svc.create(_ctx("author-1"), {
            "workspace_id": WORKSPACE, "nl_text": nl, "sql_text": sql,
            "variables": [],
        })
        # stored embedding is the real 768-dim dense vector
        assert vq.embedding is not None and len(vq.embedding) == 768
        assert sum(1 for x in vq.embedding if abs(x) > 1e-9) > 700
        await svc.submit(_ctx("author-1"), vq.id)
        await svc.approve(_ctx("steward-1"), vq.id)
        return vq.id

    revenue_id = await approved(
        "monthly revenue by region for the last year",
        "SELECT region, sum(order_total) FROM orders GROUP BY 1")
    await approved(
        "list customer support ticket categories",
        "SELECT category, count(*) FROM tickets GROUP BY 1")

    hits = await svc.search(_ctx("reader-1"), WORKSPACE,
                            "total sales revenue per region each month", top_k=5)
    assert hits, "pgvector ANN returned no rows"
    assert hits[0]["id"] == revenue_id, "real-embedding ANN ranked the wrong query"
    assert hits[0]["score"] > hits[-1]["score"] if len(hits) > 1 else True


# --- (b) semantic event published to REAL Kafka via outbox, then consumed -----


async def test_semantic_event_publishes_to_real_kafka_and_is_consumed(engine, clock):
    """Creating a model writes `model.created` to the outbox; the real
    OutboxDispatcher relays it to Redpanda (real Kafka wire protocol) and a
    consumer group reads the master envelope back. Hits: Postgres + Redpanda."""
    _require(9092, "Redpanda")
    _require(6379, "Redis")
    from windrose_common.kafka import KafkaConfig, KafkaConsumer, KafkaProducerClient

    unique = uuid.uuid4().hex[:8]
    topic = f"semantic.events.v1.it{unique}"

    settings = make_settings()
    settings.use_real_adapters = True     # real Kafka bus + Redis dedup + outbox
    settings.events_topic = topic         # isolate this test's stream
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    container = build_container(
        settings, mode="sql", session_factory=session_factory, clock=clock,
        # keep sibling/embedding doubles — this test exercises the event path only
        dataset_client=None, query_client=FakeQueryServiceClient(),
        embeddings=LocalHashEmbedding(768),
    )

    # A recording consumer group on the isolated topic.
    seen: list[dict] = []

    async def handler(envelope: dict) -> None:
        seen.append(envelope)

    class _Null:
        async def already_processed(self, *_a): return False
        async def mark_processed(self, *_a): return None

    producer = KafkaProducerClient(KafkaConfig())
    await producer.start()
    consumer = KafkaConsumer(topic, f"it-{unique}", handler, _Null(), producer,
                             cfg=KafkaConfig(), max_retries=1)
    await consumer.start()
    try:
        model, _version = await container.model_service.create(
            _ctx("author-1"),
            {"workspace_id": WORKSPACE, "name": f"sales_{unique}",
             "definition": SALES_DEFINITION},
        )
        # Relay committed outbox rows to REAL Kafka.
        relayed = await container.outbox_dispatcher.run_once()
        assert relayed >= 1

        stats = await consumer.consume_batch(max_messages=1, timeout_ms=8000)
        assert stats.processed == 1, f"consume stats: {stats}"
        assert seen and seen[0]["event_type"] == "model.created"
        assert seen[0]["tenant_id"] == TENANT_A
        assert seen[0]["resource_urn"].endswith(model.id)
    finally:
        await consumer.stop()
        await producer.stop()
        await container.bus.aclose()
        await container.dedup.aclose()


# --- (c) authorization decision via the REAL OPA container --------------------


async def test_opa_authz_decision_via_real_container():
    """The real OPA client posts the Redis permissions projection as `input` to
    the OPA data API; OPA evaluates the real Rego bundle. A present admin
    projection allows; a missing per-action projection denies. Hits: OPA + Redis.
    """
    _require(8281, "OPA")
    _require(6379, "Redis")
    from windrose_common.opaclient import projection_key
    from windrose_common.redisx import RedisProjection, build_redis

    from app.api.auth import OpaAuthzClient, Principal

    unique = uuid.uuid4().hex[:8]
    tenant = f"tenant-{unique}"
    redis = build_redis(settings_url := "redis://localhost:6379/0")
    projection = RedisProjection(redis)
    await projection.put(
        projection_key(tenant, "u1", "semantic.model.read", ""),
        {
            "action_known": True, "action_scoped": False,
            "autonomous_enabled": False,
            "flags": {"found": True, "admin": True, "ws_admin": []},
            "tenant_actions": {"found": False, "actions": []},
            "workspace": {"assigned": False, "actions": [], "archived": False},
            "resource": {"found": False, "level": "", "archived": False},
            "workspace_archived_tenant": False,
        },
    )
    client = OpaAuthzClient(redis_url=settings_url)
    principal = Principal(sub="u1", tenant_id=tenant, typ="user", scopes=[])
    try:
        assert await client.allow(principal, "semantic.model.read", None) is True
        # No projection for this action -> empty input -> deny.
        assert await client.allow(principal, "semantic.model.delete", None) is False
    finally:
        await client.aclose()
        await redis.aclose()
