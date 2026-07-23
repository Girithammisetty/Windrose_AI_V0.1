from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "eval-service"}


@router.get("/readyz")
async def readyz():
    return {"status": "ready"}


@router.get("/metrics")
async def metrics():
    from datacern_common.metricsx import REGISTRY
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(REGISTRY.render(), media_type="text/plain; version=0.0.4")
