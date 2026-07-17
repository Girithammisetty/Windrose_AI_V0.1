"""Promotion workflow: request (202), decision (approve/reject/edit), history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.api.idempotency import idempotent
from app.api.schemas import DecisionRequest, PromoteRequest, page_envelope

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.post("/models/{model_id}/versions/{version}/promote", status_code=202)
async def promote(
    request: Request, response: Response, model_id: str, version: int, body: PromoteRequest,
    principal: Principal = Depends(require("experiment.model.update")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        result = await c.promotion_service.promote(ctx, model_id, version, body.model_dump())
        return 202, {"operation_id": result["operation_id"],
                     "data": {"promotion_id": result["promotion_id"],
                              "status": result["status"]}}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.post("/promotions/{promotion_id}/decision")
async def decide(
    request: Request, promotion_id: str, body: DecisionRequest,
    principal: Principal = Depends(require("experiment.promotion.approve")),
):
    c = _c(request)
    result = await c.promotion_service.decide(
        principal.ctx(request.state.trace_id), promotion_id, body.decision,
        message=body.message, target_stage=body.target_stage)
    return {"data": result}


@router.get("/models/{model_id}/versions/{version}/promotions")
async def list_promotions(
    request: Request, model_id: str, version: int,
    principal: Principal = Depends(require("experiment.model.read")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    page = await c.promotion_service.list_promotions(
        principal.ctx(request.state.trace_id), model_id, version, limit, cursor)
    return page_envelope(page.items, page.next_cursor, page.has_more)
