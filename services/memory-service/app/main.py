"""FastAPI application factory.

Real runtime (``MEM_USE_REAL_ADAPTERS=true``, the default) registers the action
catalog with rbac at startup, runs the Kafka consumers (learning-loop topics)
and the transactional-outbox relay to Redpanda as background workers, and
cancels them on shutdown (pipeline-orchestrator's ``_start_workers`` pattern).
Unit tests build fake-adapter containers, which skip the workers entirely.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.middleware import AuthMiddleware
from app.api.routes import admin, corpora, health, internal, memories
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
            logger.exception("memory action registration error")
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
        # Dispose any async engines the container created (sql / runtime mode).
        for engine in app.state.container.extras.get("engines", []):
            await engine.dispose()


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
        logger.exception("memory kafka producer start failed")

    if container.outbox_dispatcher is not None:
        dispatcher = container.outbox_dispatcher

        async def relay_loop():
            while True:
                try:
                    n = await dispatcher.run_once()
                except Exception:  # noqa: BLE001
                    logger.exception("memory outbox relay error")
                    n = 0
                await asyncio.sleep(0.2 if n else 1.0)

        tasks.append(asyncio.create_task(relay_loop()))
        logger.info("memory outbox relay started")

    for con in container.kafka_consumers:
        try:
            await con.start()
            started.append(con)

            async def consume_loop(c=con):
                while True:
                    try:
                        await c.consume_batch(100, timeout_ms=2000)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        logger.exception("memory kafka consumer error")
                        await asyncio.sleep(1.0)

            tasks.append(asyncio.create_task(consume_loop()))
        except Exception:  # noqa: BLE001
            logger.exception("failed to start memory kafka consumer")
    if started:
        logger.info("memory kafka consumers started (%d topics)", len(started))
    return tasks, started


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    app = FastAPI(title="memory-service", version="0.1.0", docs_url="/docs",
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

    configure_tracing("memory-service")
    # Trace runs outermost, then Auth.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(RedMiddleware, service="memory-service")
    instrument_app(app, "memory-service")
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(memories.router)
    app.include_router(corpora.router)
    app.include_router(admin.router)
    app.include_router(internal.router)
    return app


app = create_app()
