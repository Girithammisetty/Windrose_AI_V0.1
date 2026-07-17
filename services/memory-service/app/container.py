"""Dependency wiring.

Two axes (mirrors semantic-service/dataset-service):
* ``mode`` selects the store — ``"sql"`` (RUNTIME DEFAULT: pgvector
  schema-per-tenant + RLS) or ``"memory"`` (in-process double, tests only). When
  omitted, ``mode`` is derived from ``settings`` (``store_mode`` override, else
  sql when ``use_real_adapters``).
* ``settings.use_real_adapters`` selects the cross-cutting adapters — ``True``
  (the RUNTIME DEFAULT) wires the real adapters against local infra (Ollama
  embeddings, Redpanda, Redis, OPA); ``False`` (set only by tests) wires the
  in-memory doubles.

``create_app`` / ``app.main:app`` build a fully real container (sql store + real
adapters) by default — no in-memory double is reachable from the running binary.
Async engines are created lazily (no connection at import); the store connects on
first request. No stub is reachable from the real path (CONVENTIONS.md END STATE).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.adapters.embeddings import LocalHashEmbedding
from app.adapters.membership import InMemoryMembership
from app.adapters.pending import InMemoryPendingQueue
from app.adapters.pii import RegexAnonymizer, RegexPiiScanner
from app.adapters.screening import PatternInjectionScreener
from app.adapters.session_store import InMemorySessionStore
from app.api.auth import LocalScopeAuthz, TokenVerifier
from app.config import Settings
from app.domain.ports import ServiceDeps
from app.domain.services import (
    AdminService,
    CorpusService,
    ErasureService,
    PolicyService,
    ProvisioningService,
    RetentionService,
    RetrievalService,
    SessionService,
    WriteService,
)
from app.events.bus import InMemoryDedupStore, InMemoryEventBus
from app.events.consumer import CONSUMED_TOPICS, MemoryEventConsumer
from app.store.memory import MemoryStore as InMemoryStore
from app.utils import Clock


@dataclass
class Container:
    settings: Settings
    clock: Clock
    deps: ServiceDeps
    store: Any
    bus: Any
    dedup: Any
    token_verifier: TokenVerifier
    authz: Any
    write_service: WriteService
    retrieval_service: RetrievalService
    session_service: SessionService
    corpus_service: CorpusService
    erasure_service: ErasureService
    retention_service: RetentionService
    policy_service: PolicyService
    admin_service: AdminService
    provisioning: ProvisioningService
    consumer: MemoryEventConsumer
    kafka_consumers: list = field(default_factory=list)
    outbox_dispatcher: Any = None
    extras: dict = field(default_factory=dict)


def build_container(
    settings: Settings | None = None,
    *,
    mode: str | None = None,
    session_factory=None,
    admin_session_factory=None,
    clock: Clock | None = None,
    embedder=None,
    screener=None,
    session_store=None,
    membership=None,
    pending=None,
) -> Container:
    settings = settings or Settings()
    clock = clock or Clock()
    real = settings.use_real_adapters
    # Resolve the store mode from settings when the caller didn't pin it: sql
    # (Postgres+pgvector) is the runtime default, memory only for the unit tier.
    if mode is None:
        mode = settings.store_mode or ("sql" if real else "memory")

    engines: list = []

    # Event bus
    if real:
        from app.events.bus import KafkaEventBus

        bus = KafkaEventBus(settings.kafka_bootstrap_servers)
    else:
        bus = InMemoryEventBus()

    # Store
    if mode == "memory":
        store = InMemoryStore()
    elif mode == "sql":
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.store.sql import SqlMemoryStore, make_engine

        # Build the async engine + session factories from settings when the
        # caller didn't inject them (the runtime path). Engines are lazy, so no
        # connection happens here — the store connects on first request.
        if session_factory is None:
            app_engine = make_engine(settings.database_url)
            engines.append(app_engine)
            session_factory = async_sessionmaker(app_engine, expire_on_commit=False)
        if admin_session_factory is None:
            if settings.admin_database_url == settings.database_url:
                admin_session_factory = session_factory
            else:
                admin_engine = make_engine(settings.admin_database_url)
                engines.append(admin_engine)
                admin_session_factory = async_sessionmaker(
                    admin_engine, expire_on_commit=False)
        store = SqlMemoryStore(session_factory, admin_session_factory)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    # Dedup
    if real:
        from app.events.bus import RedisDedupStore

        dedup = RedisDedupStore(settings.redis_url)
    else:
        dedup = InMemoryDedupStore()

    # Cross-cutting adapters
    if embedder is None:
        if real:
            from app.adapters.embeddings import OpenAIEmbeddingClient

            embedder = OpenAIEmbeddingClient(
                settings.embeddings_base_url, model=settings.embeddings_model,
                api_key=settings.embeddings_api_key)
        else:
            embedder = LocalHashEmbedding(settings.embedding_dim)
    if screener is None:
        screener = PatternInjectionScreener()
    if session_store is None:
        if real:
            from app.adapters.session_store import RedisSessionStore

            session_store = RedisSessionStore(
                settings.redis_url, ttl_seconds=settings.session_ttl_seconds)
        else:
            session_store = InMemorySessionStore()
    if membership is None:
        if real:
            from app.adapters.membership import RedisMembershipChecker

            membership = RedisMembershipChecker(settings.redis_url)
        else:
            membership = InMemoryMembership()
    if pending is None:
        if real:
            from app.adapters.pending import RedisPendingQueue

            pending = RedisPendingQueue(settings.redis_url)
        else:
            pending = InMemoryPendingQueue()

    deps = ServiceDeps(
        settings=settings, clock=clock, store=store, embedder=embedder,
        screener=screener, pii=RegexPiiScanner(), anonymizer=RegexAnonymizer(),
        session_store=session_store, membership=membership, pending=pending,
    )

    write_service = WriteService(deps)
    retrieval_service = RetrievalService(deps)
    session_service = SessionService(deps)
    corpus_service = CorpusService(deps)
    erasure_service = ErasureService(deps)
    retention_service = RetentionService(deps)
    policy_service = PolicyService(deps)
    admin_service = AdminService(deps)
    provisioning = ProvisioningService(deps)

    consumer = MemoryEventConsumer(deps, dedup)

    kafka_consumers: list = []
    outbox_dispatcher = None
    if real:
        from app.events.consumer import KafkaMemoryConsumer

        for topic in CONSUMED_TOPICS:
            kafka_consumers.append(KafkaMemoryConsumer(
                topic, consumer, bus.producer,
                bootstrap_servers=settings.kafka_bootstrap_servers))
        if mode == "sql":
            from app.store.sql import OutboxDispatcher

            outbox_dispatcher = OutboxDispatcher(session_factory, bus)
    else:
        for topic in CONSUMED_TOPICS:
            bus.subscribe(topic, consumer.handle)

    authz = _build_opa_authz(settings) if real else LocalScopeAuthz()

    extras: dict = {"mode": mode, "engines": engines}
    if session_factory is not None:
        extras["session_factory"] = session_factory
    return Container(
        settings=settings, clock=clock, deps=deps, store=store, bus=bus, dedup=dedup,
        token_verifier=TokenVerifier(settings), authz=authz,
        write_service=write_service, retrieval_service=retrieval_service,
        session_service=session_service, corpus_service=corpus_service,
        erasure_service=erasure_service, retention_service=retention_service,
        policy_service=policy_service, admin_service=admin_service,
        provisioning=provisioning, consumer=consumer,
        kafka_consumers=kafka_consumers, outbox_dispatcher=outbox_dispatcher,
        extras=extras,
    )


def _build_opa_authz(settings: Settings):
    from app.api.auth import OpaAuthzClient

    return OpaAuthzClient(settings.opa_url, redis_url=settings.redis_url)
