"""Health/readiness (MASTER-FR-051). /readyz?tenant= gates provisioning (BR-14)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response, tenant: str | None = None):
    """Readiness (MASTER-FR-051): verifies the store backend is reachable; with
    ?tenant= also checks the tenant schema is provisioned (BR-14). Returns 503
    when not ready."""
    container = request.app.state.container
    try:
        db_ok = await container.store.ping()
    except Exception:  # noqa: BLE001 — any store error => not ready
        db_ok = False
    body: dict = {"status": "ready" if db_ok else "unavailable", "db": db_ok}
    if tenant:
        tenant_ready = db_ok and await container.provisioning.ready(tenant)
        body.update({"tenant": tenant, "ready": tenant_ready,
                     "status": "ready" if tenant_ready else "provisioning"})
        if not tenant_ready:
            response.status_code = 503
    elif not db_ok:
        response.status_code = 503
    return body


@router.get("/metrics")
async def metrics():
    from datacern_common.metricsx import REGISTRY
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(REGISTRY.render(), media_type="text/plain; version=0.0.4")
