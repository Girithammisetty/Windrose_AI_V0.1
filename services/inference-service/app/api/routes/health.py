"""Health + metrics endpoints (MASTER-FR-051)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> dict:
    checks = {}
    container = request.app.state.container
    settings = container.settings
    if settings.use_real_adapters:
        # readiness probes DB connectivity; other deps are probed lazily.
        try:
            from sqlalchemy import text

            sf = container.extras.get("session_factory")
            if sf is not None:
                async with sf() as session:
                    await session.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["db"] = f"error: {exc}"
            return {"status": "degraded", "checks": checks}
    return {"status": "ok", "checks": checks}


@router.get("/metrics")
async def metrics() -> Response:
    from windrose_common.metricsx import REGISTRY
    return Response(content=REGISTRY.render(), media_type="text/plain; version=0.0.4")
