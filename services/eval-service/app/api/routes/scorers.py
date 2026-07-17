from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import Principal, require
from app.api.schemas import ScorerCreate, ScorerPatch, data
from app.api.serialize import dump, dump_page

router = APIRouter(prefix="/api/v1")


def _ctx(request, principal):
    return principal.ctx(getattr(request.state, "trace_id", None))


@router.post("/scorers", status_code=201)
async def create_scorer(
    request: Request,
    body: ScorerCreate,
    principal: Principal = Depends(require("eval.scorer.admin")),
):
    svc = request.app.state.container.scorer_service
    return data(dump(await svc.register(_ctx(request, principal), body.model_dump())))


@router.patch("/scorers/{scorer_id}")
async def patch_scorer(
    request: Request,
    scorer_id: str,
    body: ScorerPatch,
    version: int | None = None,
    principal: Principal = Depends(require("eval.scorer.admin")),
):
    svc = request.app.state.container.scorer_service
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return data(dump(await svc.update(_ctx(request, principal), scorer_id, patch, version)))


@router.post("/scorers/{scorer_key}/versions/{version}/activate")
async def activate_scorer(
    request: Request,
    scorer_key: str,
    version: int,
    principal: Principal = Depends(require("eval.scorer.admin")),
):
    svc = request.app.state.container.scorer_service
    return data(dump(await svc.activate(_ctx(request, principal), scorer_key, version)))


@router.get("/scorers")
async def list_scorers(
    request: Request,
    limit: int = Query(200, le=200),
    cursor: str | None = None,
    principal: Principal = Depends(require("eval.scorer.admin")),
):
    svc = request.app.state.container.scorer_service
    return dump_page(await svc.list(_ctx(request, principal), limit, cursor))
