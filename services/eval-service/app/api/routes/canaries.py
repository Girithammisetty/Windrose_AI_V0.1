from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import Principal, require
from app.api.schemas import CanaryCreate, CanarySamples, data
from app.api.serialize import dump

router = APIRouter(prefix="/api/v1")


def _ctx(request, principal):
    return principal.ctx(getattr(request.state, "trace_id", None))


@router.post("/canaries", status_code=201)
async def create_canary(
    request: Request,
    body: CanaryCreate,
    principal: Principal = Depends(require("eval.canary.manage")),
):
    svc = request.app.state.container.canary_service
    return data(dump(await svc.create(_ctx(request, principal), body.model_dump())))


@router.post("/canaries/{comparison_id}/samples")
async def ingest_samples(
    request: Request,
    comparison_id: str,
    body: CanarySamples,
    principal: Principal = Depends(require("eval.canary.manage")),
):
    svc = request.app.state.container.canary_service
    c = await svc.ingest_samples(_ctx(request, principal), comparison_id, body.paired_scores)
    return data(dump(c))


@router.get("/canaries/{comparison_id}")
async def get_canary(
    request: Request,
    comparison_id: str,
    principal: Principal = Depends(require("eval.canary.manage")),
):
    svc = request.app.state.container.canary_service
    return data(dump(await svc.get(_ctx(request, principal), comparison_id)))


@router.post("/canaries/{comparison_id}/stop")
async def stop_canary(
    request: Request,
    comparison_id: str,
    principal: Principal = Depends(require("eval.canary.manage")),
):
    svc = request.app.state.container.canary_service
    return data(dump(await svc.stop(_ctx(request, principal), comparison_id)))
