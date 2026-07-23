"""FastAPI application factory.

``uvicorn app.main:app`` serves this module-level ``app``. With
``EVAL_USE_REAL_ADAPTERS=true`` it wires the real runtime container — Postgres
(RLS), Redis dedup, the Redpanda/Kafka bus, real OPA authz, the real ai-gateway
judge client and the DuckDB fixture warehouse — per CONVENTIONS.md END STATE (no
runtime stubs). Otherwise it falls back to the in-memory dev wiring."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from datacern_common.logging import configure_json_logging
from fastapi import FastAPI

from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.middleware import AuthMiddleware
from app.api.routes import (
    canaries,
    cases,
    ci,
    datasets,
    gates,
    health,
    runs,
    scorers,
    suites,
    trends,
)
from app.config import Settings
from app.container import Container, build_container
from app.domain.entities import CallCtx

configure_json_logging("eval-service")  # MASTER-FR-050: JSON stdout, mirrors Go's slog handler
logger = logging.getLogger(__name__)

# Flywheel + SLO source topics (BRD §6).
_FLYWHEEL_TOPICS = [
    ("semantic.events.v1", "verified-query"),
    ("ai.proposal.v1", "proposals"),
    ("ai.agent_run.v1", "agent-run-slo"),
    ("ai.token_usage.v1", "token-usage-slo"),
    ("ai.tool_invoked.v1", "tool-slo"),
]


def _real_sql_container() -> Container:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.store.sql import make_engine

    settings = Settings()
    engine = make_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    container = build_container(settings, mode="sql", session_factory=session_factory)
    container.extras["engine"] = engine
    return container


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        container = _real_sql_container() if Settings().use_real_adapters else build_container()

    async def _lifespan(app: FastAPI):
        settings = container.settings
        tasks: list[asyncio.Task] = []
        consumers: list = []
        producer = None
        # Seed the built-in scorer registry rows for the platform tenant.
        platform_tenant = settings.register_tenant_id or "00000000-0000-0000-0000-000000000000"
        try:
            await container.scorer_service.seed_builtins(
                CallCtx(tenant_id=platform_tenant, actor={"type": "service", "id": "eval-service"})
            )
        except Exception:  # noqa: BLE001
            logger.exception("eval scorer registry seed error")
        if settings.use_real_adapters:
            try:
                from app.registration import register_actions

                await register_actions(settings)
            except Exception:  # noqa: BLE001
                logger.exception("eval action registration error")
            # Outbox dispatcher (relays committed eval.events.v1 rows to Redpanda).
            engine = container.extras.get("engine")
            if engine is not None:
                from sqlalchemy.ext.asyncio import async_sessionmaker

                from app.store.sql import OutboxDispatcher

                sf = async_sessionmaker(engine, expire_on_commit=False)
                dispatcher = OutboxDispatcher(sf, container.bus)

                async def _relay_loop():
                    while True:
                        try:
                            n = await dispatcher.run_once()
                        except Exception:  # noqa: BLE001
                            logger.exception("outbox relay error")
                            n = 0
                        await asyncio.sleep(0.5 if n else 1.0)

                tasks.append(asyncio.create_task(_relay_loop()))
            # Flywheel + SLO Kafka consumers.
            try:
                from datacern_common.kafka import KafkaConfig, KafkaProducerClient

                from app.events.consumer import KafkaTopicConsumer

                producer = KafkaProducerClient(
                    KafkaConfig(bootstrap_servers=settings.kafka_bootstrap_servers)
                )
                await producer.start()
                for topic, suffix in _FLYWHEEL_TOPICS:
                    c = KafkaTopicConsumer(
                        container.flywheel_handler,
                        container.dedup,
                        producer,
                        topic=topic,
                        group_suffix=suffix,
                        bootstrap_servers=settings.kafka_bootstrap_servers,
                    )
                    await c.start()
                    consumers.append(c)
                    tasks.append(asyncio.create_task(c.run()))
                logger.info("eval flywheel/SLO consumers started (%d topics)", len(consumers))
            except Exception:  # noqa: BLE001
                logger.exception("eval flywheel/SLO consumer startup failed")
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
                # CancelledError is a BaseException — suppress(Exception) misses it.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            for c in consumers:
                with contextlib.suppress(Exception):
                    await c.stop()
            if producer is not None:
                with contextlib.suppress(Exception):
                    await producer.stop()
            with contextlib.suppress(Exception):
                if hasattr(container.bus, "aclose"):
                    await container.bus.aclose()

    app = FastAPI(title="eval-service", version="0.1.0", docs_url="/docs", lifespan=_lifespan)
    app.state.container = container
    app.state.settings = container.settings
    app.state.token_verifier = container.token_verifier
    app.state.authz = container.authz

    from datacern_common.metricsx import RedMiddleware, instrument_app
    from datacern_common.otelx import configure_tracing
    configure_tracing("eval-service")
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(RedMiddleware, service="eval-service")
    instrument_app(app, "eval-service")
    install_error_handlers(app)

    for module in (health, datasets, cases, scorers, suites, runs, gates, ci, canaries, trends):
        app.include_router(module.router)
    return app


app = create_app()
