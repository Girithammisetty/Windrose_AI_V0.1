"""FastAPI application factory.

The module-level ``app`` is what ``make run`` (``uvicorn app.main:app``) serves.
When ``AIG_USE_REAL_ADAPTERS=true`` (the default) it wires the real runtime
container — Postgres (RLS), Redis, the Ollama LLM provider, the Redpanda/Kafka
event bus and the OPA sidecar — per CONVENTIONS.md END STATE (no runtime
stubs), and runs the transactional-outbox relay (``ai.token_usage.v1`` usage
metering to Redpanda) plus the identity/usage Kafka consumers as background
workers (pipeline-orchestrator's ``_start_workers`` pattern). Otherwise it
falls back to the in-process/in-memory dev wiring (no workers).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from datacern_common.logging import configure_json_logging
from fastapi import FastAPI

from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.middleware import AuthMiddleware
from app.api.routes import admin, data_plane, health
from app.config import Settings
from app.container import Container, build_container

configure_json_logging("ai-gateway")  # MASTER-FR-050: JSON stdout, mirrors Go's slog handler
logger = logging.getLogger(__name__)

# topic -> container handler attribute driving it (in-memory bus wiring parity).
CONSUMED_TOPICS = {
    "identity.events.v1": "identity_handler",
    "usage.events.v1": "usage_handler",
}


class _PassthroughDedup:
    """datacern_common.KafkaConsumer expects already_processed/mark_processed;
    the container handlers own dedup themselves, so the runner passes through
    (no double suppression) and keeps commit/DLQ semantics."""

    async def already_processed(self, tenant_id: str, event_id: str) -> bool:
        return False

    async def mark_processed(self, tenant_id: str, event_id: str) -> None:
        return None


async def _start_workers(container: Container):
    """Start the outbox relay + identity/usage Kafka consumers as background
    tasks (real adapters only)."""
    from datacern_common.kafka import KafkaConfig, KafkaConsumer, KafkaProducerClient

    settings = container.settings
    tasks: list[asyncio.Task] = []
    consumers: list = []
    producer = KafkaProducerClient(
        KafkaConfig(bootstrap_servers=settings.kafka_bootstrap_servers))
    await producer.start()

    if container.outbox_dispatcher is not None:
        dispatcher = container.outbox_dispatcher

        async def relay_loop():
            while True:
                try:
                    n = await dispatcher.run_once()
                except Exception:  # noqa: BLE001
                    logger.exception("ai-gateway outbox relay error")
                    n = 0
                await asyncio.sleep(0.2 if n else 1.0)

        tasks.append(asyncio.create_task(relay_loop()))
        logger.info("ai-gateway outbox relay started")

    for topic, attr in CONSUMED_TOPICS.items():
        handler = getattr(container, attr)
        try:
            con = KafkaConsumer(
                topic, f"ai-gateway.{topic}", handler.handle, _PassthroughDedup(),
                producer,
                cfg=KafkaConfig(bootstrap_servers=settings.kafka_bootstrap_servers))
            await con.start()
            consumers.append(con)
            tasks.append(asyncio.create_task(con.run()))
        except Exception:  # noqa: BLE001
            logger.exception("failed to start ai-gateway consumer for %s", topic)
    if consumers:
        logger.info("ai-gateway kafka consumers started for %s",
                    list(CONSUMED_TOPICS))
    return producer, consumers, tasks


def build_runtime_container(settings: Settings | None = None) -> Container:
    """Composition root for ``make run``. Real mode drives a Postgres-backed
    (sql) container with real Redis, the Ollama provider, the Kafka bus and OPA;
    the outbox dispatcher relays committed ``ai.token_usage.v1`` rows to real
    Redpanda. Dev mode keeps the in-memory container."""
    settings = settings or Settings()
    if not settings.use_real_adapters:
        return build_container(settings)

    from datacern_common.otelx import configure_tracing
    from datacern_common.redisx import build_redis
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.store.sql import make_engine

    configure_tracing(settings.service_name)  # real OTLP exporter when enabled
    engine = make_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = build_redis(settings.redis_url)
    container = build_container(
        settings, mode="sql", session_factory=session_factory, redis=redis
    )
    container.extras["engine"] = engine
    container.extras["redis"] = redis
    return container


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_runtime_container()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks: list[asyncio.Task] = []
        consumers: list = []
        producer = None
        if container.settings.use_real_adapters:
            try:
                producer, consumers, tasks = await _start_workers(container)
            except Exception:  # noqa: BLE001
                logger.exception("ai-gateway worker startup failed")
            try:
                from app.registration import register_actions

                await register_actions(container.settings)
            except Exception:  # noqa: BLE001
                logger.exception("ai-gateway action registration error")
        yield
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for con in consumers:
            try:
                await con.stop()
            except Exception:  # noqa: BLE001
                pass
        if producer is not None:
            try:
                await producer.stop()
            except Exception:  # noqa: BLE001
                pass
        # Release real adapter resources on shutdown (no-ops in dev mode).
        bus = container.bus
        if hasattr(bus, "aclose"):
            await bus.aclose()
        provider = container.provider_client
        if hasattr(provider, "aclose"):
            await provider.aclose()
        authz = container.authz
        if hasattr(authz, "aclose"):
            await authz.aclose()
        redis = container.extras.get("redis")
        if redis is not None:
            await redis.aclose()
        engine = container.extras.get("engine")
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="ai-gateway", version="0.1.0", docs_url="/docs",
                  lifespan=lifespan)
    app.state.container = container
    app.state.settings = container.settings
    app.state.token_verifier = container.token_verifier
    app.state.authz = container.authz
    app.state.key_service = container.key_service

    # Middleware order: Trace runs first (outermost), then Auth.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(data_plane.router)
    app.include_router(admin.router)
    return app


app = create_app()
