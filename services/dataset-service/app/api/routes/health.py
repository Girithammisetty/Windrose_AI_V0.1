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
        # Static reason only — this endpoint is unauthenticated; never leak the
        # underlying exception (host/DSN/driver detail) to a probe.
        checks["store"] = "error"
    ok = all(v == "ok" for v in checks.values())
    body = {"status": "ok" if ok else "degraded", "checks": checks}
    # Return 503 when any dependency is down so k8s/LB readiness gating pulls the
    # pod from rotation — a 200 here would keep a DB-down pod serving traffic.
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/metrics")
async def metrics():
    # Real Prometheus RED exposition (MASTER-FR-050) via the shared dependency-
    # free registry fed by RedMiddleware.
    from datacern_common.metricsx import REGISTRY
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(REGISTRY.render(), media_type="text/plain; version=0.0.4")
