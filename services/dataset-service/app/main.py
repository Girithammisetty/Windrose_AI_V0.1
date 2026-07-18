"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.middleware import AuthMiddleware
from app.api.routes import datasets, entity_resolution, health, internal, lineage
from app.config import Settings
from app.container import Container, build_container

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: register the action catalog with rbac (RBC-FR-022), run the
    ingestion.completed -> auto-register-dataset Kafka consumer AND the
    transactional-outbox relay (committed dataset events -> Redpanda,
    MASTER-FR-034) as background workers. Shutdown: stop them cleanly."""
    container: Container = app.state.container
    settings = container.settings
    tasks: list[asyncio.Task] = []
    consumer = None
    producer = None
    if settings.use_real_adapters:
        try:
            from app.registration import register_actions

            await register_actions(settings)
        except Exception:  # noqa: BLE001
            logger.exception("dataset action registration error")
        try:
            from windrose_common.kafka import KafkaConfig, KafkaProducerClient

            from app.events.consumer import KafkaIngestionConsumer

            producer = KafkaProducerClient(
                KafkaConfig(bootstrap_servers=settings.kafka_bootstrap_servers)
            )
            await producer.start()
            consumer = KafkaIngestionConsumer(
                container.ingestion_handler,
                container.dedup,
                producer,
                bootstrap_servers=settings.kafka_bootstrap_servers,
            )
            await consumer.start()
            tasks.append(asyncio.create_task(consumer.run()))
            logger.info("dataset ingestion.completed consumer worker started")
        except Exception:  # noqa: BLE001
            logger.exception("failed to start ingestion consumer worker")
        # Outbox relay: committed outbox rows -> the real Kafka bus. Without
        # this the rows sit unpublished forever (learning loop severed).
        session_factory = container.extras.get("session_factory")
        if session_factory is not None:
            from app.store.sql import OutboxDispatcher

            dispatcher = OutboxDispatcher(session_factory, container.bus)

            async def relay_loop():
                while True:
                    try:
                        n = await dispatcher.run_once()
                    except Exception:  # noqa: BLE001
                        logger.exception("dataset outbox relay error")
                        n = 0
                    await asyncio.sleep(0.2 if n else 1.0)

            tasks.append(asyncio.create_task(relay_loop()))
            logger.info("dataset outbox relay started")
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
        if consumer is not None:
            try:
                await consumer.stop()
            except Exception:  # noqa: BLE001
                pass
        if producer is not None:
            try:
                await producer.stop()
            except Exception:  # noqa: BLE001
                pass


def _real_sql_container() -> Container:
    """Build a Postgres-backed container for real runtime (DST_USE_REAL_ADAPTERS).

    The default ``build_container()`` runs in ``mode="memory"`` (unit/dev), which
    keeps dataset/version/profile-pointer rows in RAM even with real object-store
    /Kafka adapters wired. In real mode we must persist to Postgres, so construct
    an async session factory over ``settings.database_url`` and select SQL mode.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config import Settings
    from app.store.sql import make_engine

    settings = Settings()
    engine = make_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return build_container(settings, mode="sql", session_factory=session_factory)


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        container = _real_sql_container() if Settings().use_real_adapters else build_container()
    app = FastAPI(title="dataset-service", version="0.1.0", docs_url="/docs", lifespan=_lifespan)
    app.state.container = container
    app.state.settings = container.settings
    app.state.token_verifier = container.token_verifier
    app.state.authz = container.authz

    # Observability (MASTER-FR-050): tracing (no-op unless WINDROSE_OTEL_ENABLED)
    # + RED metrics middleware. RED is added first so it is OUTERMOST and times
    # the whole request incl. auth.
    from windrose_common.metricsx import RedMiddleware, instrument_app
    from windrose_common.otelx import configure_tracing

    configure_tracing("dataset-service")
    # Middleware order: Trace runs first (outermost), then Auth.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(RedMiddleware, service="dataset-service")
    instrument_app(app, "dataset-service")
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(datasets.router)
    app.include_router(lineage.router)
    app.include_router(entity_resolution.router)
    app.include_router(internal.router)

    # The in-process profiler reports through the real callback endpoint.
    container.reporter.bind_app(app)
    return app


app = create_app()
