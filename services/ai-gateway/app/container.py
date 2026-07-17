"""Dependency wiring: memory (unit/dev) and sql (integration/prod) modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.adapters.embeddings import HashEmbedder
from app.adapters.guardrail_models import (
    HeuristicInjectionClassifier,
    RegexPIIAnalyzer,
)
from app.adapters.kv import InMemoryInvalidationChannel, InMemoryKV
from app.adapters.ledger import FallbackLedger, InMemoryLedger, PgLedger, RedisLedger
from app.adapters.providers import InProcessProvider
from app.api.auth import LocalScopeAuthz, TokenVerifier
from app.config import Settings
from app.domain.admission import AdmissionController
from app.domain.budgets import BudgetEngine
from app.domain.cache import SemanticCache
from app.domain.guardrails import GuardrailEngine
from app.domain.keys import KeyService
from app.domain.ladders import LadderService
from app.domain.pipeline import GatewayService
from app.domain.ports import Metrics, Tracer
from app.domain.pricing import PriceTable
from app.domain.providers_admin import HealthProber, ProviderAdminService
from app.domain.reconciliation import (
    SpendAnomalyDetector,
    UsageReconciler,
    UsageRecorder,
)
from app.domain.routing import CircuitBreaker, HealthRegistry, Router
from app.events.bus import InMemoryDedupStore, InMemoryEventBus
from app.events.consumer import IdentityEventHandler, UsageEventHandler
from app.events.envelope import make_envelope
from app.store.memory import MemoryState, memory_uow_factory
from app.utils import Clock


@dataclass
class Container:
    settings: Settings
    clock: Clock
    uow_factory: Any
    bus: InMemoryEventBus
    dedup: Any
    kv: Any
    ledger: Any
    invalidation: Any
    tracer: Tracer
    metrics: Metrics
    prices: PriceTable
    breaker: CircuitBreaker
    health: HealthRegistry
    router: Router
    provider_client: Any
    embedder: Any
    guardrails: GuardrailEngine
    cache: SemanticCache
    admission: AdmissionController
    key_service: KeyService
    ladder_service: LadderService
    budget_engine: BudgetEngine
    gateway: GatewayService
    provider_admin: ProviderAdminService
    prober: HealthProber
    usage_recorder: UsageRecorder
    reconciler: UsageReconciler
    anomaly: SpendAnomalyDetector
    identity_handler: IdentityEventHandler
    usage_handler: UsageEventHandler
    token_verifier: TokenVerifier
    authz: Any
    memory_state: MemoryState | None = None
    outbox_dispatcher: Any = None
    extras: dict = field(default_factory=dict)

    async def emit_event(self, tenant_id: str, event_type: str, payload: dict) -> None:
        """Outbox-backed event emission for engine callbacks (MASTER-FR-034)."""
        async with self.uow_factory(tenant_id) as uow:
            await uow.outbox.add(self.settings.events_topic, make_envelope(
                event_type=event_type, tenant_id=tenant_id,
                actor={"type": "service", "id": "ai-gateway"},
                resource_urn=f"wr:{tenant_id}:ai:{event_type.split('.')[0]}/-",
                payload=payload,
            ))
            await uow.commit()

    async def cross_tenant_audit(self, principal, collection: str,
                                 item_id: str) -> None:
        """MASTER-FR-003: cross-tenant access attempt → 404 + audit event. The
        global probe is only possible in memory mode; under RLS the row is
        simply invisible (the 404 stands either way)."""
        if self.memory_state is None:
            return
        owner = self.memory_state.owner_of(collection, item_id)
        if owner and owner != principal.tenant_id:
            await self.emit_event(principal.tenant_id, "security.cross_tenant_denied", {
                "collection": collection,
                "resource_id": item_id,
                "subject": principal.sub,
            })


def _default_authz(settings: Settings, redis=None):
    """OPA sidecar authz in real mode; scope-based local authz otherwise."""
    if not settings.use_real_adapters:
        return LocalScopeAuthz()
    from app.api.auth import OpaAuthzClient

    return OpaAuthzClient(settings.opa_url, redis_url=settings.redis_url)


def build_container(
    settings: Settings | None = None,
    *,
    mode: str = "memory",
    session_factory=None,
    redis=None,
    clock: Clock | None = None,
    provider_client=None,
    invalidation=None,
    sleeper=None,
    kv=None,
    ledger=None,
    bus=None,
    authz=None,
) -> Container:
    settings = settings or Settings()
    clock = clock or Clock()
    real = settings.use_real_adapters
    if bus is None:
        if real:
            from app.events.bus import KafkaEventBus

            bus = KafkaEventBus(settings.kafka_bootstrap_servers)
        else:
            bus = InMemoryEventBus()

    memory_state: MemoryState | None = None
    outbox_dispatcher = None
    if mode == "memory":
        memory_state = MemoryState(bus=bus)
        uow_factory = memory_uow_factory(memory_state)
        dedup = InMemoryDedupStore()
        kv = kv or InMemoryKV(clock)
        ledger = ledger or InMemoryLedger(clock, settings.reservation_ttl_seconds)
    elif mode == "sql":
        if session_factory is None:
            raise ValueError("sql mode requires a session_factory")
        from app.store.sql import OutboxDispatcher, SqlDedupStore, sql_uow_factory

        uow_factory = sql_uow_factory(session_factory)
        dedup = SqlDedupStore(session_factory)
        outbox_dispatcher = OutboxDispatcher(session_factory, bus)
        pg_ledger = PgLedger(session_factory, clock, settings.reservation_ttl_seconds)
        if redis is not None:
            from app.adapters.kv import RedisKV

            kv = kv or RedisKV(redis)
            redis_ledger = RedisLedger(redis, clock, settings.reservation_ttl_seconds)
            ledger = ledger or FallbackLedger(redis_ledger, pg_ledger)
        else:
            kv = kv or InMemoryKV(clock)
            ledger = ledger or pg_ledger
    else:
        raise ValueError(f"unknown mode {mode!r}")

    invalidation = invalidation or InMemoryInvalidationChannel()
    tracer = Tracer()
    metrics = Metrics()
    prices = PriceTable(version=settings.price_version)
    breaker = CircuitBreaker(settings, clock)
    health = HealthRegistry(settings)
    router = Router(settings, breaker, health)
    if provider_client is None:
        if real:
            # Provider-agnostic dispatch: one registry resolves each request's
            # deployment.provider -> its real adapter (Ollama/OpenAI-compatible/
            # Azure OpenAI/Anthropic), using the per-deployment endpoint+credential
            # from the gateway's own store. bedrock/vertex are accepted at config
            # time but raise a typed ProviderNotConfigured at execution (Rule 2).
            from app.adapters.registry import ProviderRegistry

            provider_client = ProviderRegistry(settings)
        else:
            provider_client = InProcessProvider()
    embedder = HashEmbedder(dim=settings.embedding_dim if mode == "sql" else 256)

    guardrails = GuardrailEngine(uow_factory, RegexPIIAnalyzer(),
                                 HeuristicInjectionClassifier(), settings)
    cache = SemanticCache(kv, embedder, uow_factory, clock, settings)
    admission = AdmissionController(kv, clock, settings)
    key_service = KeyService(uow_factory, clock, settings, invalidation)
    ladder_service = LadderService(uow_factory, settings)

    container_holder: dict = {}

    async def emit_event(tenant_id: str, event_type: str, payload: dict) -> None:
        await container_holder["c"].emit_event(tenant_id, event_type, payload)

    budget_engine = BudgetEngine(uow_factory, ledger, clock, settings, emit_event)
    usage_recorder = UsageRecorder()
    reconciler = UsageReconciler(usage_recorder, settings, emit_event)
    anomaly = SpendAnomalyDetector(clock, settings, emit_event)

    gateway = GatewayService(
        uow_factory=uow_factory, settings=settings, clock=clock,
        ladders=ladder_service, budgets=budget_engine, guardrails=guardrails,
        cache=cache, admission=admission, router=router, breaker=breaker,
        provider=provider_client, prices=prices, tracer=tracer, metrics=metrics,
        usage_recorder=usage_recorder, anomaly=anomaly, sleeper=sleeper,
    )
    provider_admin = ProviderAdminService(uow_factory, settings, clock)
    prober = HealthProber(uow_factory, settings, provider_client, health)
    identity_handler = IdentityEventHandler(uow_factory, dedup, key_service, cache,
                                            clock, settings)
    usage_handler = UsageEventHandler(uow_factory, dedup, clock)
    # In-process dispatch on the in-memory bus (unit/dev). The real KafkaEventBus
    # has no subscribe(); its consumers are driven by the windrose_common
    # consumer-group runner (deployment wiring), so guard the call.
    if hasattr(bus, "subscribe"):
        bus.subscribe("identity.events.v1", identity_handler.handle)
        bus.subscribe("usage.events.v1", usage_handler.handle)

    container = Container(
        settings=settings, clock=clock, uow_factory=uow_factory, bus=bus,
        dedup=dedup, kv=kv, ledger=ledger, invalidation=invalidation,
        tracer=tracer, metrics=metrics, prices=prices, breaker=breaker,
        health=health, router=router, provider_client=provider_client,
        embedder=embedder, guardrails=guardrails, cache=cache,
        admission=admission, key_service=key_service,
        ladder_service=ladder_service, budget_engine=budget_engine,
        gateway=gateway, provider_admin=provider_admin, prober=prober,
        usage_recorder=usage_recorder, reconciler=reconciler, anomaly=anomaly,
        identity_handler=identity_handler, usage_handler=usage_handler,
        token_verifier=TokenVerifier(settings),
        authz=authz or _default_authz(settings, redis),
        memory_state=memory_state, outbox_dispatcher=outbox_dispatcher,
    )
    container_holder["c"] = container
    return container
