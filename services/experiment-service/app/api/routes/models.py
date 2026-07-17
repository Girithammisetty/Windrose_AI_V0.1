"""Registered models + versions + model cards (BRD §5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import PlainTextResponse

from app.api.auth import Principal, require
from app.api.schemas import CardPatch, page_envelope

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.get("/models")
async def list_models(
    request: Request,
    principal: Principal = Depends(require("experiment.model.read")),
    workspace_id: str | None = Query(default=None, alias="filter[workspace_id]"),
    stage: str | None = Query(default=None, alias="filter[stage]"),
    ids: str | None = Query(default=None, alias="filter[id]"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    # filter[id] accepts a comma-separated model-id set (IN filter) so the bff
    # dataloader can batch models without N+1. Tenant-scoped via RLS.
    id_list = ([x for x in (s.strip() for s in ids.split(",")) if x] if ids else None)
    page = await c.registry_service.list_models(
        principal.ctx(request.state.trace_id), workspace_id, stage, limit, cursor, ids=id_list)
    return page_envelope(page.items, page.next_cursor, page.has_more)


@router.get("/models/{model_id}")
async def get_model(
    request: Request, model_id: str,
    principal: Principal = Depends(require("experiment.model.read")),
):
    c = _c(request)
    return {"data": await c.registry_service.get_model(
        principal.ctx(request.state.trace_id), model_id)}


@router.get("/models/{model_id}/versions/{version}")
async def get_version(
    request: Request, model_id: str, version: int,
    principal: Principal = Depends(require("experiment.model.read")),
):
    c = _c(request)
    return {"data": await c.registry_service.get_version(
        principal.ctx(request.state.trace_id), model_id, version)}


@router.get("/models/{model_id}/versions/{version}/card")
async def get_card(
    request: Request, model_id: str, version: int,
    principal: Principal = Depends(require("experiment.model.read")),
    format: str | None = None,
):
    c = _c(request)
    card = await c.card_service.get_card(
        principal.ctx(request.state.trace_id), model_id, version, format)
    if format == "markdown":
        return PlainTextResponse(card, media_type="text/markdown")
    return {"data": card}


@router.patch("/models/{model_id}/versions/{version}/card")
async def patch_card(
    request: Request, model_id: str, version: int, body: CardPatch,
    principal: Principal = Depends(require("experiment.model_card.update")),
):
    c = _c(request)
    card = await c.card_service.patch_overlay(
        principal.ctx(request.state.trace_id), model_id, version,
        body.model_dump(exclude_unset=True))
    return {"data": card}
