"""FastAPI application factory.

Real runtime (``SEM_USE_REAL_ADAPTERS=true``, the default) registers the action
catalog with rbac at startup, runs the Kafka consumers (dataset/chart/rbac
topics) and the transactional-outbox relay to Redpanda as background workers,
and cancels them on shutdown (pipeline-orchestrator's ``_start_workers``
pattern). Unit tests inject fake-adapter containers, which skip the workers.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.middleware import AuthMiddleware
from app.api.routes import compile as compile_routes
from app.api.routes import health, models, tools, verified_queries
from app.config import Settings
from app.container import Container, build_container

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    container: Container = app.state.container
    settings = container.settings
    tasks: list[asyncio.Task] = []
    started_consumers: list = []
    if settings.use_real_adapters:
        try:
            from app.registration import register_actions

            await register_actions(settings)
        except Exception:  # noqa: BLE001
            logger.exception("semantic action registration error")
        tasks, started_consumers = await _start_workers(container)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for con in started_consumers:
            try:
                await con.stop()
            except Exception:  # noqa: BLE001
                pass
        if settings.use_real_adapters and hasattr(container.bus, "aclose"):
            try:
                await container.bus.aclose()
            except Exception:  # noqa: BLE001
                pass


async def _start_workers(container: Container):
    """Start the outbox relay + Kafka consumers as background tasks (real
    adapters only; the container builds them but nothing else runs them)."""
    tasks: list[asyncio.Task] = []
    started: list = []

    # The consumers' DLQ path shares the bus's idempotent producer; start it
    # up front (KafkaProducerClient.start is idempotent — the outbox publish
    # path reuses it).
    try:
        await container.bus.producer.start()
    except Exception:  # noqa: BLE001
        logger.exception("semantic kafka producer start failed")

    if container.outbox_dispatcher is not None:
        dispatcher = container.outbox_dispatcher

        async def relay_loop():
            while True:
                try:
                    n = await dispatcher.run_once()
                except Exception:  # noqa: BLE001
                    logger.exception("semantic outbox relay error")
                    n = 0
                await asyncio.sleep(0.2 if n else 1.0)

        tasks.append(asyncio.create_task(relay_loop()))
        logger.info("semantic outbox relay started")

    for con in container.kafka_consumers:
        try:
            await con.start()
            started.append(con)
            tasks.append(asyncio.create_task(con.run()))
        except Exception:  # noqa: BLE001
            logger.exception("failed to start semantic kafka consumer for %s",
                             getattr(con, "topic", "?"))
    if started:
        logger.info("semantic kafka consumers started (%d topics)", len(started))
    return tasks, started


def build_runtime_container() -> Container:
    """Runtime composition root. When ``SEM_USE_REAL_ADAPTERS`` is set the service
    wires its real adapters (Redpanda, Redis, OPA, Ollama/ai-gateway, sibling
    HTTP) against a Postgres-backed (RLS) store — no stub is reachable
    (CONVENTIONS.md END STATE). Otherwise it falls back to the in-memory dev
    wiring. Tests inject their own container into ``create_app`` and never take
    this path."""
    settings = Settings()
    if settings.use_real_adapters:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.store.sql import make_engine

        engine = make_engine(settings.database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return build_container(settings, mode="sql", session_factory=session_factory)
    return build_container(settings, mode="memory")


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_runtime_container()
    app = FastAPI(title="semantic-service", version="0.1.0", docs_url="/docs",
                  lifespan=_lifespan)
    app.state.container = container
    app.state.settings = container.settings
    app.state.token_verifier = container.token_verifier
    app.state.authz = container.authz

    # Observability (MASTER-FR-050): tracing (no-op unless WINDROSE_OTEL_ENABLED)
    # + RED metrics middleware. RED is added last so it is OUTERMOST and times
    # the whole request incl. auth.
    from windrose_common.metricsx import RedMiddleware, instrument_app
    from windrose_common.otelx import configure_tracing

    configure_tracing("semantic-service")
    # Middleware order: Trace runs first (outermost), then Auth.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(RedMiddleware, service="semantic-service")
    instrument_app(app, "semantic-service")
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(models.router)
    app.include_router(compile_routes.router)
    app.include_router(verified_queries.router)
    app.include_router(tools.router)
    return app


app = create_app()
