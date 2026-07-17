"""Dependency wiring: memory (unit/dev) and sql (integration/prod) modes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.adapters.profiler_runner import InProcessProfilerRunner, sign_callback
from app.adapters.search_index import InMemorySearchIndex, PostgresFTSSearchIndex
from app.api.auth import LocalScopeAuthz, OpaAuthzClient, TokenVerifier
from app.config import Settings
from app.domain.ports import ProfileJobSpec
from app.domain.services import (
    DatasetService,
    LineageService,
    ProfileService,
    RetentionService,
    ServiceDeps,
    VersionService,
)
from app.events.bus import InMemoryDedupStore, InMemoryEventBus
from app.events.consumer import IngestionEventHandler
from app.mcp.facade import McpFacade
from app.store.memory import MemoryState, memory_uow_factory
from app.utils import Clock

PROFILER_SPIFFE = "spiffe://windrose/ns/data/sa/profiler"


class HttpCallbackReporter:
    """Reports profiler results by PUTting the internal callback endpoint with
    the SPIFFE header + HMAC body signature — the same contract the
    containerized profiler uses (DST-FR-023)."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    def bind_app(self, app) -> None:
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://dataset-service"
        )

    async def __call__(self, spec: ProfileJobSpec, body: dict) -> None:
        if self._client is None:
            raise RuntimeError("HttpCallbackReporter not bound to an app")
        raw = json.dumps(body, default=str).encode()
        response = await self._client.put(
            f"/internal/v1/profiles/{spec.profile_id}",
            content=raw,
            headers={
                "content-type": "application/json",
                "x-client-spiffe-id": PROFILER_SPIFFE,
                "x-profiler-signature": sign_callback(spec.callback_token, raw),
            },
        )
        if response.status_code >= 500:
            response.raise_for_status()


@dataclass
class Container:
    settings: Settings
    clock: Clock
    deps: ServiceDeps
    bus: InMemoryEventBus
    dedup: Any
    catalog: Any
    object_store: Any
    search_index: Any
    runner: Any
    reporter: HttpCallbackReporter
    token_verifier: TokenVerifier
    authz: Any
    dataset_service: DatasetService
    version_service: VersionService
    profile_service: ProfileService
    lineage_service: LineageService
    retention_service: RetentionService
    ingestion_handler: IngestionEventHandler
    mcp: McpFacade
    memory_state: MemoryState | None = None
    extras: dict = field(default_factory=dict)


def build_container(
    settings: Settings | None = None,
    *,
    mode: str = "memory",
    session_factory=None,
    clock: Clock | None = None,
    runner=None,
) -> Container:
    settings = settings or Settings()
    clock = clock or Clock()
    # Real runtime uses the Kafka bus + Redis dedup; the unit/dev tier keeps the
    # in-memory bus (which also dispatches to the in-process consumer handler).
    if settings.use_real_adapters:
        from app.events.bus import KafkaEventBus, RedisDedupStore

        bus = KafkaEventBus(settings.kafka_bootstrap_servers)
    else:
        bus = InMemoryEventBus()

    memory_state: MemoryState | None = None
    if mode == "memory":
        memory_state = MemoryState()
        uow_factory = memory_uow_factory(memory_state)
        search_index = InMemorySearchIndex()
        dedup = InMemoryDedupStore()
    elif mode == "sql":
        if session_factory is None:
            raise ValueError("sql mode requires a session_factory")
        from app.store.sql import SqlDedupStore, sql_uow_factory

        uow_factory = sql_uow_factory(session_factory)
        search_index = PostgresFTSSearchIndex(_search_session_factory(session_factory))
        dedup = SqlDedupStore(session_factory)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    if settings.use_real_adapters:
        dedup = RedisDedupStore(settings.redis_url)

    # Swappable dependency provider registries (Phase 3): select the catalog +
    # object-store backends by name (config-driven, independently). Defaults are
    # derived from `use_real_adapters` so existing deployments are unchanged.
    from app.adapters.registry import resolve_catalog, resolve_object_store

    catalog = resolve_catalog(settings)
    object_store = resolve_object_store(settings)

    deps = ServiceDeps(
        settings=settings,
        clock=clock,
        uow_factory=uow_factory,
        catalog=catalog,
        object_store=object_store,
        search_index=search_index,
    )

    dataset_service = DatasetService(deps)
    version_service = VersionService(deps)
    profile_service = ProfileService(deps)
    lineage_service = LineageService(deps)
    retention_service = RetentionService(deps)

    reporter = HttpCallbackReporter()
    if runner is None:
        runner = InProcessProfilerRunner(
            catalog, object_store, reporter,
            profiler_version=settings.profiler_version,
            clock=clock, max_rows=settings.profile_sample_max_rows,
        )
    deps.runner_provider = lambda: runner

    ingestion_handler = IngestionEventHandler(
        deps, dedup, dataset_service, version_service, lineage_service
    )
    # In-process dispatch on the in-memory bus (unit/dev). In real mode the
    # KafkaIngestionConsumer runner drives ingestion_handler.handle from Kafka.
    if hasattr(bus, "subscribe"):
        bus.subscribe("ingestion.events.v1", ingestion_handler.handle)

    authz = OpaAuthzClient(settings.opa_url, redis_url=settings.redis_url) if (
        settings.use_real_adapters
    ) else LocalScopeAuthz()

    mcp = McpFacade(dataset_service, version_service, profile_service, lineage_service)

    return Container(
        settings=settings,
        clock=clock,
        deps=deps,
        bus=bus,
        dedup=dedup,
        catalog=catalog,
        object_store=object_store,
        search_index=search_index,
        runner=runner,
        reporter=reporter,
        token_verifier=TokenVerifier(settings),
        authz=authz,
        dataset_service=dataset_service,
        version_service=version_service,
        profile_service=profile_service,
        lineage_service=lineage_service,
        retention_service=retention_service,
        ingestion_handler=ingestion_handler,
        mcp=mcp,
        memory_state=memory_state,
        extras={"session_factory": session_factory} if session_factory else {},
    )


def _search_session_factory(session_factory):
    """Wrap the plain session factory into tenant-bound sessions for FTS."""
    from contextlib import asynccontextmanager

    from sqlalchemy import text

    @asynccontextmanager
    async def tenant_session(tenant_id: str):
        session = session_factory()
        try:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            yield session
            await session.commit()
        finally:
            await session.close()

    return tenant_session
