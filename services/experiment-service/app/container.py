"""Dependency wiring: memory (unit/dev) and sql (integration/prod) modes.

app.main builds the SQL + real-adapter container by DEFAULT (real MLflow, real
Kafka+outbox, real Redis dedup, real OPA, RLS-bound Postgres). The in-memory
mode is reachable only from unit tests, never from app.main.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.adapters.artifacts import LocalArtifactSigner, S3ArtifactSigner
from app.adapters.mlflow_client import LocalMlflowClient, MlflowClient
from app.api.auth import LocalScopeAuthz, OpaAuthzClient, TokenVerifier
from app.config import Settings
from app.domain.services import (
    CardService,
    CompareService,
    ExperimentService,
    MirrorService,
    PromotionService,
    QueryService,
    ReconciliationService,
    RegistryService,
    RunService,
    ServiceDeps,
)
from app.events.bus import InMemoryDedupStore, InMemoryEventBus
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
    token_verifier: TokenVerifier
    authz: Any
    experiment_service: ExperimentService
    run_service: RunService
    mirror_service: MirrorService
    reconciliation_service: ReconciliationService
    compare_service: CompareService
    query_service: QueryService
    registry_service: RegistryService
    promotion_service: PromotionService
    card_service: CardService
    mcp: McpFacade
    memory_state: MemoryState | None = None
    extras: dict = field(default_factory=dict)


def build_container(settings: Settings | None = None, *, mode: str = "memory",
                    session_factory=None, clock: Clock | None = None,
                    mlflow=None) -> Container:
    settings = settings or Settings()
    clock = clock or Clock()
    real = settings.use_real_adapters
    extras: dict = {"gauges": {}}

    # -- event bus + dedup ---------------------------------------------------
    if real:
        from app.events.bus import KafkaEventBus, RedisDedupStore

        bus = KafkaEventBus(settings.kafka_bootstrap_servers)
    else:
        bus = InMemoryEventBus()

    memory_state: MemoryState | None = None
    if mode == "memory":
        memory_state = MemoryState()
        uow_factory = memory_uow_factory(memory_state, bus)
        dedup = InMemoryDedupStore()
    elif mode == "sql":
        if session_factory is None:
            raise ValueError("sql mode requires a session_factory")
        from app.store.sql import OutboxDispatcher, SqlDedupStore, sql_uow_factory

        uow_factory = sql_uow_factory(session_factory)
        dedup = SqlDedupStore(session_factory)
        extras["session_factory"] = session_factory
        extras["outbox_dispatcher"] = OutboxDispatcher(session_factory, bus)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    if real:
        dedup = RedisDedupStore(settings.redis_url)

    # -- MLflow client -------------------------------------------------------
    if mlflow is None:
        mlflow = MlflowClient(settings.mlflow_tracking_uri) if real else LocalMlflowClient()

    # -- artifact signer -----------------------------------------------------
    if real:
        artifact_signer = S3ArtifactSigner(
            endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key, region=settings.s3_region)
    else:
        artifact_signer = LocalArtifactSigner()

    deps = ServiceDeps(settings=settings, clock=clock, uow_factory=uow_factory,
                       mlflow=mlflow, artifact_signer=artifact_signer)

    experiment_service = ExperimentService(deps)
    run_service = RunService(deps)
    mirror_service = MirrorService(deps)
    reconciliation_service = ReconciliationService(deps, mirror_service)
    compare_service = CompareService(deps)
    query_service = QueryService(deps)
    registry_service = RegistryService(deps)
    promotion_service = PromotionService(deps)
    card_service = CardService(deps)
    mcp = McpFacade(query_service, compare_service, registry_service, promotion_service,
                    card_service)

    authz = OpaAuthzClient(settings.opa_url, redis_url=settings.redis_url) if real \
        else LocalScopeAuthz()

    return Container(
        settings=settings, clock=clock, deps=deps, bus=bus, dedup=dedup,
        token_verifier=TokenVerifier(settings), authz=authz,
        experiment_service=experiment_service, run_service=run_service,
        mirror_service=mirror_service, reconciliation_service=reconciliation_service,
        compare_service=compare_service, query_service=query_service,
        registry_service=registry_service, promotion_service=promotion_service,
        card_service=card_service, mcp=mcp, memory_state=memory_state, extras=extras)
