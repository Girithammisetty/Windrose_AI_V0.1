"""Health + metrics endpoints (MASTER-FR-051)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import PlainTextResponse

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    container = request.app.state.container
    checks = {"store": "ok", "ledger": "ok"}
    try:
        async with container.uow_factory(container.settings.platform_tenant_id) as uow:
            await uow.providers.list(1, None)
    except Exception:  # noqa: BLE001
        checks["store"] = "down"
    try:
        await container.ledger.usage("bud:readyz:1970-01-01")
    except Exception:  # noqa: BLE001
        checks["ledger"] = "down"
    status = 200 if all(v == "ok" for v in checks.values()) else 503
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok" if status == 200 else "degraded",
                         "checks": checks}, status_code=status)


@router.get("/metrics")
async def metrics(request: Request):
    return PlainTextResponse(request.app.state.container.metrics.render())
