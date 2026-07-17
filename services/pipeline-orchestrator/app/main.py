"""FastAPI application factory.

Real runtime (``PPL_USE_REAL_ADAPTERS=true``) wires the Postgres-backed container +
real MLflow + the local training executor, registers the action catalog with rbac,
bootstraps the component/algorithm catalog into Postgres, starts the transactional
outbox relay to Redpanda, and runs the Kafka consumers (learning-loop
``case.disposition_applied`` + ``tenant.provisioned``) as background workers.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.middleware import AuthMiddleware
from app.api.routes import (
    algorithms,
    components,
    health,
    internal,
    pipelines,
    runs,
    schedules,
)
from app.config import Settings
from app.container import Container, build_container

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    container: Container = app.state.container
    settings = container.settings
    tasks: list[asyncio.Task] = []
    producer = None
    consumers: list = []
    if settings.use_real_adapters:
        await _register_and_bootstrap(container, settings)
        producer, consumers, tasks = await _start_workers(container, settings)
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
        for engine in container.extras.get("engines", []):
            await engine.dispose()


async def _register_and_bootstrap(container, settings):
    try:
        from app.registration import register_actions

        await register_actions(settings)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline action registration error")
    try:
        from app.store.sql import bootstrap_catalog

        sf = container.extras.get("session_factory")
        if sf is not None:
            await bootstrap_catalog(sf, container.extras["components"],
                                    container.extras["algorithms"])
            logger.info("component + algorithm catalog bootstrapped to Postgres")
    except Exception:  # noqa: BLE001
        logger.exception("catalog bootstrap failed")


async def _start_workers(container, settings):
    from windrose_common.kafka import KafkaConfig, KafkaProducerClient

    from app.events.bus import KafkaOutboxBus
    from app.events.consumer import CONSUMED_TOPICS, KafkaPipelineConsumer
    from app.store.sql import OutboxDispatcher

    producer = KafkaProducerClient(
        KafkaConfig(bootstrap_servers=settings.kafka_bootstrap_servers))
    await producer.start()
    tasks: list[asyncio.Task] = []
    consumers: list = []

    sf = container.extras.get("session_factory")
    if sf is not None:
        dispatcher = OutboxDispatcher(sf, KafkaOutboxBus(producer))

        async def relay_loop():
            while True:
                try:
                    n = await dispatcher.run_once()
                except Exception:  # noqa: BLE001
                    logger.exception("outbox relay error")
                    n = 0
                await asyncio.sleep(0.2 if n else 1.0)

        tasks.append(asyncio.create_task(relay_loop()))
        logger.info("outbox relay started")

    if settings.scheduler_enabled:
        from app.domain.enums import RunStatus

        async def scheduler_loop():
            while True:
                await asyncio.sleep(settings.scheduler_poll_seconds)
                try:
                    fired = await container.schedule_service.fire_due()
                except Exception:  # noqa: BLE001
                    logger.exception("pipeline scheduler tick error")
                    continue
                # Drive submitted scheduled runs through the executor, fire-and-forget,
                # exactly like the /run route does after create_run.
                for run in fired:
                    if run.status == int(RunStatus.submitted):
                        container.schedule_drive(run.tenant_id, run.id)

        tasks.append(asyncio.create_task(scheduler_loop()))
        logger.info("pipeline scheduler ticker started (poll=%.0fs)",
                    settings.scheduler_poll_seconds)

    for topic in CONSUMED_TOPICS:
        try:
            con = KafkaPipelineConsumer(
                topic, container.consumer, producer,
                bootstrap_servers=settings.kafka_bootstrap_servers)
            await con.start()
            consumers.append(con)
            tasks.append(asyncio.create_task(con.run()))
        except Exception:  # noqa: BLE001
            logger.exception("failed to start consumer for %s", topic)
    logger.info("kafka consumers started for %s", CONSUMED_TOPICS)
    return producer, consumers, tasks


def _real_sql_container() -> Container:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.store.sql import make_engine

    settings = Settings()
    engine = make_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    container = build_container(settings, mode="sql", session_factory=session_factory)
    container.extras["engines"] = [engine]
    return container


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        container = _real_sql_container() if Settings().use_real_adapters else build_container()
    app = FastAPI(title="pipeline-orchestrator", version="0.1.0", docs_url="/docs",
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

    configure_tracing("pipeline-orchestrator")
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(RedMiddleware, service="pipeline-orchestrator")
    instrument_app(app, "pipeline-orchestrator")
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(pipelines.router)
    app.include_router(runs.router)
    app.include_router(schedules.router)
    app.include_router(components.router)
    app.include_router(algorithms.router)
    app.include_router(internal.router)
    app.include_router(internal.api)
    return app


app = create_app()
