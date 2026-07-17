from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import Principal, require
from app.api.schemas import SloTargets, data

router = APIRouter(prefix="/api/v1")


def _ctx(request, principal):
    return principal.ctx(getattr(request.state, "trace_id", None))


def _is_operator(principal: Principal) -> bool:
    return "*" in principal.scopes or "eval.slo.operator" in principal.scopes


@router.get("/trends")
async def trends(
    request: Request,
    agent_key: str,
    scorer: str | None = None,
    window: str = "30d",
    principal: Principal = Depends(require("eval.trends.read")),
):
    svc = request.app.state.container.trend_service
    return data(await svc.trends(_ctx(request, principal), agent_key, scorer, window))


@router.get("/slos")
async def slos(
    request: Request,
    agent_key: str,
    window: str = "24h",
    principal: Principal = Depends(require("eval.slo.read")),
):
    svc = request.app.state.container.slo_service
    return data(
        await svc.query(
            _ctx(request, principal), agent_key, window, operator=_is_operator(principal)
        )
    )


@router.post("/slos/targets")
async def set_targets(
    request: Request, body: SloTargets, principal: Principal = Depends(require("eval.slo.read"))
):
    svc = request.app.state.container.slo_service
    await svc.set_targets(
        _ctx(request, principal), body.agent_key, body.agent_version, body.targets
    )
    return data({"ok": True})
