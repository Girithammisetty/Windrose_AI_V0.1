"""Health endpoints (MASTER-FR-051)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import PlainTextResponse

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    # memory mode has no external deps; sql mode checks the DB session factory
    container = request.app.state.container
    checks = {"store": "ok"}
    session_factory = container.extras.get("session_factory")
    if session_factory is not None:
        try:
            from sqlalchemy import text

            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            checks["store"] = "unavailable"
            return {"status": "degraded", "checks": checks}
    return {"status": "ok", "checks": checks}


@router.get("/metrics")
async def metrics():
    from windrose_common.metricsx import REGISTRY

    return PlainTextResponse(REGISTRY.render(), media_type="text/plain; version=0.0.4")
