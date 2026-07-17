"""FastAPI application factory.

By default app.main wires the REAL adapters (INF_USE_REAL_ADAPTERS defaults True):
Postgres (SQLAlchemy async + RLS), the MLflow model registry, the local S3
scoring executor, Redis dedup + budget gate, the Kafka event bus, and the OPA
authz client. A background WorkerSet runs the outbox relay, the Kafka consumers,
the schedule tick and the reaper.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import TraceMiddleware, install_error_handlers
from app.api.middleware import AuthMiddleware
from app.api.routes import endpoints, health, inferences, internal, lineage, schedules
from app.config import Settings
from app.container import Container, build_container

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    container: Container = app.state.container
    settings = container.settings
    workers = None
    if settings.use_real_adapters:
        # BUG-2: set MLflow's global tracking+registry uri from config at startup so
        # models:/ resolution hits the real MLflow server (not the local file store).
        try:
            from app.adapters.mlflow_registry import set_global_mlflow_uri

            set_global_mlflow_uri(settings.mlflow_tracking_uri)
        except Exception:  # noqa: BLE001
            logger.exception("failed to set global MLflow uri")
        try:
            from app.registration import register_actions

            await register_actions(settings)
        except Exception:  # noqa: BLE001
            logger.exception("inference action registration error")
        session_factory = container.extras.get("session_factory")
        if session_factory is not None:
            try:
                from app.workers import WorkerSet

                workers = WorkerSet(container, session_factory)
                await workers.start()
                logger.info("inference workers started (outbox, consumers, scheduler, reaper)")
            except Exception:  # noqa: BLE001
                logger.exception("failed to start inference workers")
    try:
        yield
    finally:
        if workers is not None:
            await workers.stop()


def _real_sql_container() -> Container:
    """Postgres-backed real-adapter container for the runtime."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.store.sql import make_engine

    settings = Settings()
    engine = make_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    container = build_container(settings, mode="sql", session_factory=session_factory)
    _wire_launch_run(container)
    return container


def _wire_launch_run(container: Container) -> None:
    """Real execution launcher: run scoring off the request path as a task."""
    background: set[asyncio.Task] = set()

    async def launch(tenant_id: str, job_id: str) -> None:
        task = asyncio.create_task(container.inference.execute_job(tenant_id, job_id))
        background.add(task)
        task.add_done_callback(background.discard)

    container.inference._launch_run = launch  # noqa: SLF001


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        settings = Settings()
        if settings.use_real_adapters:
            container = _real_sql_container()
        else:
            container = build_container(settings)
    app = FastAPI(title="inference-service", version="0.1.0", docs_url="/docs",
                  lifespan=_lifespan)
    app.state.container = container
    app.state.settings = container.settings
    app.state.token_verifier = container.token_verifier
    app.state.authz = container.authz

    from windrose_common.metricsx import RedMiddleware, instrument_app
    from windrose_common.otelx import configure_tracing
    configure_tracing("inference-service")
    app.add_middleware(AuthMiddleware)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(RedMiddleware, service="inference-service")
    instrument_app(app, "inference-service")
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(inferences.router)
    app.include_router(schedules.router)
    app.include_router(lineage.router)
    app.include_router(endpoints.router)
    app.include_router(internal.router)
    return app


app = create_app()
