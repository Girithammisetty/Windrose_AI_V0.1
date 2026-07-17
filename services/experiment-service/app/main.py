"""FastAPI application factory.

By DEFAULT (``EXP_USE_REAL_ADAPTERS`` true) app.main builds the SQL + real-adapter
container: RLS-bound Postgres, real MLflow REST, real Kafka + transactional
outbox relay, real Redis dedup, real OPA. Background workers (reconciliation
sweep, promotion-expiry, inbox applier, outbox relay) and the pipeline-events
consumer run as durable in-process tasks. No stub is reachable from this path.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.middleware import AuthMiddleware
from app.api.routes import experiments, health, internal, models, promotions, runs
from app.config import Settings
from app.container import Container, build_container

logger = logging.getLogger(__name__)


def _real_sql_container() -> Container:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.store.sql import make_engine

    settings = Settings()
    engine = make_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    container = build_container(settings, mode="sql", session_factory=session_factory)
    container.extras["engine"] = engine
    return container


@asynccontextmanager
async def _lifespan(app: FastAPI):
    container: Container = app.state.container
    settings = container.settings
    stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    producer = None
    consumers: list = []
    if settings.use_real_adapters:
        try:
            from app.registration import register_actions

            await register_actions(settings)
        except Exception:  # noqa: BLE001
            logger.exception("experiment action registration error")

        from app.workers.loops import expiry_loop, inbox_loop, outbox_loop, reconcile_loop

        tasks.append(asyncio.create_task(outbox_loop(container, stop)))
        tasks.append(asyncio.create_task(inbox_loop(container, stop)))
        tasks.append(asyncio.create_task(expiry_loop(container, stop)))
        tasks.append(asyncio.create_task(reconcile_loop(container, stop)))

        try:
            from windrose_common.kafka import KafkaConfig, KafkaProducerClient

            from app.events.consumer import (
                DatasetEventHandler,
                KafkaDatasetConsumer,
                KafkaPipelineConsumer,
                PipelineEventHandler,
            )

            producer = KafkaProducerClient(
                KafkaConfig(bootstrap_servers=settings.kafka_bootstrap_servers))
            await producer.start()

            pipeline_consumer = KafkaPipelineConsumer(
                PipelineEventHandler(container.run_service, container.dedup),
                container.dedup, producer,
                bootstrap_servers=settings.kafka_bootstrap_servers)
            await pipeline_consumer.start()
            consumers.append(pipeline_consumer)
            tasks.append(asyncio.create_task(pipeline_consumer.run(stop)))

            dataset_consumer = KafkaDatasetConsumer(
                DatasetEventHandler(container.card_service, container.dedup),
                container.dedup, producer,
                bootstrap_servers=settings.kafka_bootstrap_servers)
            await dataset_consumer.start()
            consumers.append(dataset_consumer)
            tasks.append(asyncio.create_task(dataset_consumer.run(stop)))

            logger.info("experiment pipeline + dataset consumers + workers started")
        except Exception:  # noqa: BLE001
            logger.exception("failed to start consumer workers")
    try:
        yield
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for c in consumers:
            try:
                await c.stop()
            except Exception:  # noqa: BLE001
                pass
        if producer is not None:
            try:
                await producer.stop()
            except Exception:  # noqa: BLE001
                pass
        engine = container.extras.get("engine")
        if engine is not None:
            await engine.dispose()


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        container = _real_sql_container() if Settings().use_real_adapters else build_container()
    app = FastAPI(title="experiment-service", version="0.1.0", docs_url="/docs",
                  lifespan=_lifespan)
    app.state.container = container
    app.state.settings = container.settings
    app.state.token_verifier = container.token_verifier
    app.state.authz = container.authz

    from windrose_common.metricsx import RedMiddleware, instrument_app
    from windrose_common.otelx import configure_tracing
    configure_tracing("experiment-service")
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(RedMiddleware, service="experiment-service")
    instrument_app(app, "experiment-service")
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(experiments.router)
    app.include_router(runs.router)
    app.include_router(models.router)
    app.include_router(promotions.router)
    app.include_router(internal.router)
    return app


app = create_app()
