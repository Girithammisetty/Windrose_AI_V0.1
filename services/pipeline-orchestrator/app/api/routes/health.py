"""Health endpoints (MASTER-FR-051)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    c = request.app.state.container
    checks = {}
    try:
        async with c.deps.uow_factory("00000000-0000-0000-0000-000000000000"):
            checks["store"] = "ok"
    except Exception:  # noqa: BLE001
        # Static reason only — unauthenticated probe, never leak the exception.
        checks["store"] = "error"
    ok = all(v == "ok" for v in checks.values())
    body = {"status": "ok" if ok else "degraded", "checks": checks,
            "executor_backend": c.settings.executor_backend}
    # 503 when degraded so k8s readiness gating pulls a DB-down pod from rotation.
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/metrics")
async def metrics():
    from fastapi.responses import PlainTextResponse

    from windrose_common.metricsx import REGISTRY

    return PlainTextResponse(REGISTRY.render(), media_type="text/plain; version=0.0.4")
