"""Dependency wiring.

Two axes:

* ``mode`` selects the store — ``"memory"`` (unit/dev, in-process state) or
  ``"sql"`` (integration/prod, SQLAlchemy + Postgres RLS).
* ``settings.use_real_adapters`` selects the cross-cutting adapters —
  ``False`` wires the local test doubles (StaticDatasetClient,
  FakeQueryServiceClient, LocalHashEmbedding, in-memory bus/dedup, LocalScopeAuthz)
  for the unit tier; ``True`` wires the real adapters against local,
  protocol-compatible infra — Redpanda (Kafka bus + consumers), Redis (dedup),
  OPA (authz), Ollama/ai-gateway (embeddings) and the sibling HTTP services. No
  stub is reachable from the real path (CONVENTIONS.md END STATE). ``create_app``
  defaults to the real wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.adapters.dataset_client import StaticDatasetClient
from app.adapters.embeddings import LocalHashEmbedding
from app.adapters.query_client import FakeQueryServiceClient
from app.api.auth import LocalScopeAuthz, TokenVerifier
from app.config import Settings
from app.domain.services import (
    BootstrapService,
    CompileService,
    ModelService,
    ServiceDeps,
    VerifiedQueryService,
    VersionService,
)
from app.events.bus import InMemoryDedupStore, InMemoryEventBus
from app.events.consumer import CHART_TOPIC, DATASET_TOPIC, RBAC_TOPIC, SemanticEventConsumer
from app.mcp.facade import McpFacade
from app.store.memory import MemoryState, memory_uow_factory
from app.utils import Clock


@dataclass
class Container:
    settings: Settings
    clock: Clock
    deps: ServiceDeps
    bus: Any
    dedup: Any
    dataset_client: Any
    query_client: Any
    embeddings: Any
    token_verifier: TokenVerifier
    authz: Any
    model_service: ModelService
    version_service: VersionService
    compile_service: CompileService
    verified_query_service: VerifiedQueryService
    bootstrap_service: BootstrapService
    consumer: SemanticEventConsumer
    mcp: McpFacade
    memory_state: MemoryState | None = None
    kafka_consumers: list = field(default_factory=list)
    outbox_dispatcher: Any = None
    extras: dict = field(default_factory=dict)


def build_container(
    settings: Settings | None = None,
    *,
    mode: str = "memory",
    session_factory=None,
    clock: Clock | None = None,
    dataset_client=None,
    query_client=None,
    embeddings=None,
) -> Container:
    settings = settings or Settings()
    clock = clock or Clock()
    real = settings.use_real_adapters

    # Event bus: real Kafka (Redpanda) producer, or the in-memory bus that also
    # dispatches synchronously to the in-process consumer handler (unit/dev).
    if real:
        from app.events.bus import KafkaEventBus

        bus = KafkaEventBus(settings.kafka_bootstrap_servers)
    else:
        bus = InMemoryEventBus()

    memory_state: MemoryState | None = None
    if mode == "memory":
        memory_state = MemoryState()
        uow_factory = memory_uow_factory(memory_state)
        dedup = InMemoryDedupStore()
    elif mode == "sql":
        if session_factory is None:
            raise ValueError("sql mode requires a session_factory")
        from app.store.sql import SqlDedupStore, sql_uow_factory

        uow_factory = sql_uow_factory(session_factory)
        dedup = SqlDedupStore(session_factory)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    # Real Redis dedup owns duplicate suppression (MASTER-FR-032) in the real path.
    if real:
        from app.events.bus import RedisDedupStore

        dedup = RedisDedupStore(settings.redis_url)

    # Adapters: explicit overrides win (tests), else real vs. local doubles.
    if dataset_client is None:
        if real:
            from app.adapters.dataset_client import HttpDatasetClient

            dataset_client = HttpDatasetClient(
                settings.dataset_service_url,
                spiffe_id=settings.service_spiffe_id,
                spiffe_header=settings.spiffe_header,
                timeout_s=settings.http_timeout_s,
            )
        else:
            dataset_client = StaticDatasetClient()
    if query_client is None:
        if real:
            from app.adapters.query_client import HttpQueryServiceClient

            query_client = HttpQueryServiceClient(
                settings.query_service_url,
                timeout_s=settings.http_timeout_s,
            )
        else:
            query_client = FakeQueryServiceClient()
    if embeddings is None:
        if real:
            from app.adapters.embeddings import OpenAIEmbeddingClient

            embeddings = OpenAIEmbeddingClient(
                settings.embeddings_base_url,
                model=settings.embeddings_model,
                api_key=settings.embeddings_api_key,
            )
        else:
            embeddings = LocalHashEmbedding(settings.embedding_dim)

    deps = ServiceDeps(
        settings=settings,
        clock=clock,
        uow_factory=uow_factory,
        dataset_client=dataset_client,
        query_client=query_client,
        embeddings=embeddings,
    )

    compile_service = CompileService(deps)
    model_service = ModelService(deps)
    version_service = VersionService(deps, compile_service)
    verified_query_service = VerifiedQueryService(deps)
    bootstrap_service = BootstrapService(deps)

    consumer = SemanticEventConsumer(deps, dedup)
    kafka_consumers: list = []
    outbox_dispatcher = None
    if real:
        # Real transport: one Kafka consumer group per subscribed topic driving
        # the transport-agnostic handler; the outbox dispatcher relays committed
        # semantic events + ai.tool_invoked.v1 audits to Redpanda (MASTER-FR-034).
        from app.events.consumer import KafkaSemanticConsumer

        for topic in (DATASET_TOPIC, CHART_TOPIC, RBAC_TOPIC):
            kafka_consumers.append(
                KafkaSemanticConsumer(
                    topic, consumer, bus.producer,
                    bootstrap_servers=settings.kafka_bootstrap_servers,
                )
            )
        if mode == "sql":
            from app.store.sql import OutboxDispatcher

            outbox_dispatcher = OutboxDispatcher(session_factory, bus)
    else:
        # In-process dispatch on the in-memory bus (unit/dev).
        for topic in (DATASET_TOPIC, CHART_TOPIC, RBAC_TOPIC):
            bus.subscribe(topic, consumer.handle)

    authz = (
        _build_opa_authz(settings) if real else LocalScopeAuthz()
    )

    mcp = McpFacade(deps, model_service, compile_service, verified_query_service)

    return Container(
        settings=settings,
        clock=clock,
        deps=deps,
        bus=bus,
        dedup=dedup,
        dataset_client=dataset_client,
        query_client=query_client,
        embeddings=embeddings,
        token_verifier=TokenVerifier(settings),
        authz=authz,
        model_service=model_service,
        version_service=version_service,
        compile_service=compile_service,
        verified_query_service=verified_query_service,
        bootstrap_service=bootstrap_service,
        consumer=consumer,
        mcp=mcp,
        memory_state=memory_state,
        kafka_consumers=kafka_consumers,
        outbox_dispatcher=outbox_dispatcher,
        extras={"session_factory": session_factory} if session_factory else {},
    )


def _build_opa_authz(settings: Settings):
    from app.api.auth import OpaAuthzClient

    return OpaAuthzClient(settings.opa_url, redis_url=settings.redis_url)
