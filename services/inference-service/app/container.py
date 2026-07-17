"""Dependency wiring: ``memory`` (unit/dev) and ``sql`` (integration/prod) modes.

``use_real_adapters`` (INF_USE_REAL_ADAPTERS) selects the real adapters against
local infra: MLflow model registry, the local S3 scoring executor, Redis dedup +
budget gate, and the Kafka event bus. app.main wires real adapters by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.api.auth import LocalScopeAuthz, OpaAuthzClient, TokenVerifier
from app.config import Settings
from app.domain.ports import ServiceDeps
from app.domain.schedules import ScheduleService
from app.domain.services import InferenceService
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
    registry: Any
    executor: Any
    budget_gate: Any
    token_verifier: TokenVerifier
    authz: Any
    inference: InferenceService
    schedules: ScheduleService
    mcp: McpFacade
    memory_state: MemoryState | None = None
    extras: dict = field(default_factory=dict)


def build_container(
    settings: Settings | None = None,
    *,
    mode: str = "memory",
    session_factory=None,
    clock: Clock | None = None,
    registry=None,
    executor=None,
    launch_run=None,
) -> Container:
    settings = settings or Settings()
    clock = clock or Clock()

    memory_state: MemoryState | None = None
    if mode == "memory":
        memory_state = MemoryState()
        uow_factory = memory_uow_factory(memory_state)
    elif mode == "sql":
        if session_factory is None:
            raise ValueError("sql mode requires a session_factory")
        from app.store.sql import sql_uow_factory

        uow_factory = sql_uow_factory(session_factory)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    if settings.use_real_adapters:
        from app.events.bus import RedisDedupStore

        bus = InMemoryEventBus()  # replaced by KafkaEventBus in the outbox relay path
        dedup = RedisDedupStore(settings.redis_url)
    else:
        bus = InMemoryEventBus()
        dedup = InMemoryDedupStore()

    if registry is None:
        if settings.use_real_adapters:
            from app.adapters.mlflow_registry import MlflowModelRegistry

            registry = MlflowModelRegistry(
                settings.mlflow_tracking_uri, s3_endpoint_url=settings.s3_endpoint_url,
                s3_access_key=settings.s3_access_key, s3_secret_key=settings.s3_secret_key)
        else:
            raise ValueError("a model registry double is required in non-real mode")

    if executor is None:
        if settings.use_real_adapters:
            from app.adapters.executor import LocalScoringExecutor

            executor = LocalScoringExecutor(
                datasets_bucket=settings.datasets_bucket,
                s3_endpoint_url=settings.s3_endpoint_url, s3_access_key=settings.s3_access_key,
                s3_secret_key=settings.s3_secret_key, s3_region=settings.s3_region,
                mlflow_tracking_uri=settings.mlflow_tracking_uri)
        else:
            raise ValueError("a scoring executor double is required in non-real mode")

    if settings.use_real_adapters:
        from app.adapters.budget import RedisBudgetGate

        budget_gate = RedisBudgetGate(settings.redis_url)
    else:
        from app.adapters.budget import InMemoryBudgetGate

        budget_gate = InMemoryBudgetGate()

    deps = ServiceDeps(
        settings=settings, clock=clock, uow_factory=uow_factory, registry=registry,
        executor=executor, dedup=dedup, notifier=None, budget_gate=budget_gate)

    inference = InferenceService(deps, launch_run=launch_run)
    schedules = ScheduleService(deps, inference)
    mcp = McpFacade(inference, schedules)

    authz = (
        OpaAuthzClient(settings.opa_url, redis_url=settings.redis_url)
        if settings.use_real_adapters else LocalScopeAuthz()
    )

    return Container(
        settings=settings, clock=clock, deps=deps, bus=bus, dedup=dedup, registry=registry,
        executor=executor, budget_gate=budget_gate, token_verifier=TokenVerifier(settings),
        authz=authz, inference=inference, schedules=schedules, mcp=mcp,
        memory_state=memory_state,
        extras={"session_factory": session_factory} if session_factory else {})
