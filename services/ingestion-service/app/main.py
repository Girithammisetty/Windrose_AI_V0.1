"""FastAPI app factory."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.api.context import new_trace_id
from app.api.errors import register_error_handlers
from app.api.routes import (
    connections,
    health,
    hooks,
    ingestions,
    internal,
    schedules,
    uploads,
    writebacks,
)
from app.config import Settings
from app.container import Container, build_container

logger = logging.getLogger(__name__)

_request_counter: Counter[tuple[str, str, int]] = Counter()


async def _outbox_relay_loop(container) -> None:
    """Drain the transactional outbox to Kafka (MASTER-FR-034). Runs as a
    background worker: publishes committed ingestion.events.v1 rows (e.g.
    ingestion.completed, which dataset-service consumes) and marks them
    published. Drains across tenants via the narrow SECURITY DEFINER
    outbox-only RLS bypass in publish_pending (see app/events/outbox.py and
    migration 0005) -- the runtime role itself stays NOSUPERUSER NOBYPASSRLS
    (migration 0004)."""
    from app.events.outbox import publish_pending

    while True:
        try:
            async with container.db.session_factory() as session:
                n = await publish_pending(session, container.publisher)
            await asyncio.sleep(0.3 if n else 1.0)
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001
            logger.exception("outbox relay error")
            await asyncio.sleep(1.0)


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        relay_task = None
        # Deploy-time action-catalog registration (RBC-FR-022) so OPA's catalog
        # knows every action this service authorizes against (`action_known`).
        if container.settings.adapter_mode == "real":
            try:
                from app.registration import register_actions

                await register_actions(container.settings)
            except Exception:  # noqa: BLE001
                logger.exception("ingestion action registration error")
            # Outbox -> Kafka relay worker (drains ingestion.events.v1).
            relay_task = asyncio.create_task(_outbox_relay_loop(container))
            logger.info("ingestion outbox relay worker started")
        try:
            yield
        finally:
            if relay_task is not None:
                relay_task.cancel()
                try:
                    await relay_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await container.db.dispose()

    app = FastAPI(
        title="ingestion-service",
        version="0.1.0",
        description="Windrose ingestion-service (BRD 03): connections, ingestion jobs, "
        "resumable uploads, schedules, webhook push.",
        lifespan=lifespan,
    )
    app.state.container = container
    register_error_handlers(app)

    # Observability (MASTER-FR-050): tracing only. This service keeps its own
    # hand-rolled http_requests_total exposition below, so we deliberately do
    # NOT add RedMiddleware (avoids a duplicate metric-name clash).
    from windrose_common.metricsx import instrument_app
    from windrose_common.otelx import configure_tracing

    configure_tracing("ingestion-service")
    instrument_app(app, "ingestion-service")

    @app.middleware("http")
    async def _trace_and_metrics(request: Request, call_next) -> Response:
        trace_id = new_trace_id()  # MASTER-FR-028: X-Trace-Id on every response
        response: Response = await call_next(request)
        response.headers.setdefault("X-Trace-Id", trace_id)
        route = getattr(request.scope.get("route"), "path", request.url.path)
        _request_counter[(request.method, route, response.status_code)] += 1
        return response

    @app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    async def metrics() -> str:
        """Prometheus exposition (RED metrics baseline, MASTER-FR-050/051).
        TODO(wave-2): full OTel + histogram instrumentation."""
        lines = [
            "# HELP http_requests_total Total HTTP requests.",
            "# TYPE http_requests_total counter",
        ]
        for (method, route, status), count in sorted(_request_counter.items()):
            lines.append(
                f'http_requests_total{{method="{method}",route="{route}",'
                f'status="{status}"}} {count}'
            )
        return "\n".join(lines) + "\n"

    app.include_router(health.router)
    app.include_router(internal.router)
    for router in (
        connections.router,
        ingestions.router,
        uploads.router,
        schedules.router,
        hooks.router,
        writebacks.router,
    ):
        app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
