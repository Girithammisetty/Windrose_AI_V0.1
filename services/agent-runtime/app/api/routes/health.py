from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/metrics")
async def metrics():
    from fastapi.responses import PlainTextResponse

    from windrose_common.metricsx import REGISTRY
    return PlainTextResponse(REGISTRY.render(), media_type="text/plain; version=0.0.4")


@router.get("/readyz")
async def readyz(request: Request):
    """Reports the ACTUAL execution mode so a silent downgrade (Temporal
    unreachable -> inline runs, failed catalog seed, dead outbox relay) is
    visible to ops instead of being swallowed at startup."""
    c = request.app.state.container
    extras = c.extras
    return {
        "status": "ready",
        "mode": extras.get("mode"),
        "execution": "temporal" if "temporal_client" in extras else "inline",
        "temporal_connected": "temporal_client" in extras,
        "catalog_seeded": extras.get("catalog_seeded"),
        "outbox_relay": extras.get("outbox_relay", False),
    }
