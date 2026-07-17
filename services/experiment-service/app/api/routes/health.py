"""Health + readiness + metrics (MASTER-FR-051)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request):
    checks = {}
    container = request.app.state.container
    # DB check (real mode): a trivial round-trip through a tenant session.
    try:
        sf = container.extras.get("session_factory")
        if sf is not None:
            from sqlalchemy import text

            async with sf() as s:
                await s.execute(text("SELECT 1"))
            checks["db"] = "ok"
        else:
            checks["db"] = "memory"
    except Exception as exc:  # noqa: BLE001
        checks["db"] = f"error: {exc}"
    return {"status": "ok" if all(v in ("ok", "memory") for v in checks.values())
            else "degraded", "checks": checks}


@router.get("/metrics")
async def metrics(request: Request):
    container = request.app.state.container
    gauges = container.extras.get("gauges", {})
    lines = [
        "# HELP mlflow_mirror_lag_seconds Age of the mirror vs MLflow (per last sweep).",
        "# TYPE mlflow_mirror_lag_seconds gauge",
        f"mlflow_mirror_lag_seconds {gauges.get('mlflow_mirror_lag_seconds', 0)}",
        "# HELP mlflow_mirror_drift_total Repairs applied by the last reconciliation sweep.",
        "# TYPE mlflow_mirror_drift_total gauge",
        f"mlflow_mirror_drift_total {gauges.get('mlflow_mirror_drift_total', 0)}",
    ]
    from windrose_common.metricsx import REGISTRY
    body = "\n".join(lines) + "\n" + REGISTRY.render()
    return Response(body, media_type="text/plain; version=0.0.4")
