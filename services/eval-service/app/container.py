"""Dependency wiring: memory (unit/dev) and sql (integration/prod) modes.

Real mode (``EVAL_USE_REAL_ADAPTERS=true``) wires the shared ``windrose_common``
adapters against local infra — Redpanda (Kafka bus), Redis (dedup), OPA (authz)
— plus the real ai-gateway judge client and the DuckDB fixture warehouse, per
CONVENTIONS.md END STATE (no runtime stubs). Otherwise the in-memory test doubles
are wired for the unit/dev tier."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.adapters.candidate_provider import (
    AgentRuntimeReplayProvider,
    InlineCandidateProvider,
)
from app.adapters.fixture_warehouse import DuckDbFixtureWarehouse
from app.adapters.judge_client import AiGatewayJudgeClient
from app.api.auth import LocalScopeAuthz, OpaAuthzClient, TokenVerifier
from app.config import Settings
from app.domain.online import OnlineSamplingService
from app.domain.runner import EvalRunner
from app.domain.scorers.registry import ScorerRegistry
from app.domain.services import (
    CanaryService,
    CaseService,
    DatasetService,
    GateService,
    RunService,
    ScorerService,
    ServiceDeps,
    SloService,
    SuiteService,
    TrendService,
)
from app.events.bus import InMemoryEventBus
from app.events.consumer import FlywheelHandler
from app.store.memory import InMemoryDedupStore, MemoryState, memory_uow_factory
from app.utils import Clock


@dataclass
class Container:
    settings: Settings
    clock: Clock
    deps: ServiceDeps
    uow_factory: Any
    registry: ScorerRegistry
    warehouse: Any
    judge_client: Any
    default_provider: Any
    bus: Any
    dedup: Any
    token_verifier: TokenVerifier
    authz: Any
    dataset_service: DatasetService
    case_service: CaseService
    scorer_service: ScorerService
    suite_service: SuiteService
    run_service: RunService
    gate_service: GateService
    canary_service: CanaryService
    trend_service: TrendService
    slo_service: SloService
    online_service: OnlineSamplingService
    flywheel_handler: FlywheelHandler
    memory_state: MemoryState | None = None
    extras: dict = field(default_factory=dict)

    def candidate_provider(self, candidate_outputs: dict | None):
        if candidate_outputs:
            return InlineCandidateProvider(candidate_outputs)
        return self.default_provider


def build_container(
    settings: Settings | None = None,
    *,
    mode: str = "memory",
    session_factory=None,
    clock: Clock | None = None,
    judge_client=None,
    warehouse=None,
    bus=None,
) -> Container:
    settings = settings or Settings()
    clock = clock or Clock()

    # ---- store ----
    memory_state: MemoryState | None = None
    if mode == "memory":
        memory_state = MemoryState()
        uow_factory = memory_uow_factory(memory_state)
        dedup = InMemoryDedupStore(memory_state)
    elif mode == "sql":
        if session_factory is None:
            raise ValueError("sql mode requires a session_factory")
        from app.store.sql import SqlDedupStore, sql_uow_factory

        uow_factory = sql_uow_factory(session_factory)
        dedup = SqlDedupStore(session_factory)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    # ---- event bus ----
    if bus is None:
        if settings.use_real_adapters:
            from app.events.bus import KafkaEventBus

            bus = KafkaEventBus(settings.kafka_bootstrap_servers)
        else:
            bus = InMemoryEventBus()

    # ---- real Redis dedup in real mode ----
    if settings.use_real_adapters and mode == "sql":
        from app.events.bus import RedisDedupStore

        dedup = RedisDedupStore(settings.redis_url)

    # ---- fixture warehouse (DuckDB) ----
    if warehouse is None:
        warehouse = DuckDbFixtureWarehouse(
            settings.fixture_warehouse_dir,
            ceiling_s=settings.sql_execution_ceiling_s,
            row_cap=settings.fixture_row_cap,
        )

    # ---- judge client (real ai-gateway) ----
    if judge_client is None:
        judge_client = AiGatewayJudgeClient(
            settings.ai_gateway_url,
            chat_path=settings.ai_gateway_chat_path,
            model=settings.ai_gateway_model,
            virtual_key=settings.ai_gateway_virtual_key,
            request_class=settings.judge_request_class,
            jwt_signing_key_pem=settings.judge_jwt_signing_key_pem,
            jwt_signing_kid=settings.judge_jwt_signing_kid,
            jwt_issuer=settings.jwt_issuer,
            jwt_audience=settings.jwt_audience,
            timeout_s=settings.judge_timeout_s,
        )

    registry = ScorerRegistry(warehouse=warehouse, judge_client=judge_client)

    # ---- candidate provider (real agent-runtime replay, if configured) ----
    # agent-runtime's /replay endpoint (ART-FR-015) requires a verified platform
    # token; eval calls it as the service principal ``svc:eval-service`` carrying
    # the canonical replay action ``ai.agent_session.execute``. Reuse the same
    # signing key eval already uses for its judge JWT (harness IdP key; verified by
    # agent-runtime's JWKS/PEM). Without a key we pass no provider and the endpoint
    # will 401 — which the provider surfaces as CANDIDATE_UNAVAILABLE (honest).
    def _replay_jwt(tenant_id: str) -> str:
        import time

        import jwt as pyjwt

        now = int(time.time())
        claims = {
            "sub": "svc:eval-service",
            "typ": "service",
            "tenant_id": tenant_id,
            "scopes": ["ai.agent_session.execute"],
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + 300,
        }
        headers = (
            {"kid": settings.judge_jwt_signing_kid}
            if settings.judge_jwt_signing_kid
            else None
        )
        return pyjwt.encode(
            claims, settings.judge_jwt_signing_key_pem, algorithm="RS256", headers=headers
        )

    default_provider = (
        AgentRuntimeReplayProvider(
            settings.agent_runtime_url,
            jwt_provider=(_replay_jwt if settings.judge_jwt_signing_key_pem else None),
        )
        if settings.agent_runtime_url
        else InlineCandidateProvider({})
    )

    def runner_factory(candidate_provider) -> EvalRunner:
        return EvalRunner(registry, candidate_provider, clock=clock)

    deps = ServiceDeps(
        settings=settings,
        clock=clock,
        uow_factory=uow_factory,
        registry=registry,
        runner_factory=runner_factory,
        events_topic=settings.events_topic,
    )

    dataset_service = DatasetService(deps)
    case_service = CaseService(deps)
    scorer_service = ScorerService(deps)
    suite_service = SuiteService(deps)
    run_service = RunService(deps)
    gate_service = GateService(deps)
    canary_service = CanaryService(deps)
    trend_service = TrendService(deps)
    slo_service = SloService(deps)
    online_service = OnlineSamplingService(deps)

    flywheel_handler = FlywheelHandler(dedup, case_service, slo_service)

    authz = (
        OpaAuthzClient(settings.opa_url, redis_url=settings.redis_url)
        if settings.use_real_adapters
        else LocalScopeAuthz()
    )

    return Container(
        settings=settings,
        clock=clock,
        deps=deps,
        uow_factory=uow_factory,
        registry=registry,
        warehouse=warehouse,
        judge_client=judge_client,
        default_provider=default_provider,
        bus=bus,
        dedup=dedup,
        token_verifier=TokenVerifier(settings),
        authz=authz,
        dataset_service=dataset_service,
        case_service=case_service,
        scorer_service=scorer_service,
        suite_service=suite_service,
        run_service=run_service,
        gate_service=gate_service,
        canary_service=canary_service,
        trend_service=trend_service,
        slo_service=slo_service,
        online_service=online_service,
        flywheel_handler=flywheel_handler,
        memory_state=memory_state,
        extras={"session_factory": session_factory} if session_factory else {},
    )
