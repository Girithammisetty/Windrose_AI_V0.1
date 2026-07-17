"""Scheduled scoring endpoints (BRD §5, INF-FR-050..055)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.api.schemas import ScheduleBody, SchedulePatch, page_envelope, schedule_payload
from app.domain.ports import Filters

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.post("/schedules", status_code=201)
async def create_schedule(
    request: Request, body: ScheduleBody,
    principal: Principal = Depends(require("inference.schedule.create")),
):
    c = _c(request)
    sch = await c.schedules.create(principal.ctx(request.state.trace_id),
                                   body.model_dump(exclude_none=True))
    return {"data": schedule_payload(sch)}


@router.get("/schedules")
async def list_schedules(
    request: Request,
    principal: Principal = Depends(require("inference.schedule.read")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    page = await c.schedules.list(principal.ctx(request.state.trace_id), limit, cursor)
    return page_envelope([schedule_payload(s) for s in page.items], page.next_cursor,
                         page.has_more)


@router.get("/schedules/{schedule_id}")
async def get_schedule(
    request: Request, schedule_id: str,
    principal: Principal = Depends(require("inference.schedule.read")),
):
    c = _c(request)
    sch = await c.schedules.get(principal.ctx(request.state.trace_id), schedule_id)
    return {"data": schedule_payload(sch)}


@router.patch("/schedules/{schedule_id}")
async def patch_schedule(
    request: Request, schedule_id: str, body: SchedulePatch,
    principal: Principal = Depends(require("inference.schedule.update")),
):
    c = _c(request)
    sch = await c.schedules.update(principal.ctx(request.state.trace_id), schedule_id,
                                   body.model_dump(exclude_unset=True))
    return {"data": schedule_payload(sch)}


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    request: Request, schedule_id: str,
    principal: Principal = Depends(require("inference.schedule.delete")),
):
    c = _c(request)
    await c.schedules.delete(principal.ctx(request.state.trace_id), schedule_id)
    return Response(status_code=204)


@router.post("/schedules/{schedule_id}/pause")
async def pause_schedule(
    request: Request, schedule_id: str,
    principal: Principal = Depends(require("inference.schedule.update")),
):
    c = _c(request)
    sch = await c.schedules.pause(principal.ctx(request.state.trace_id), schedule_id)
    return {"data": schedule_payload(sch)}


@router.post("/schedules/{schedule_id}/resume")
async def resume_schedule(
    request: Request, schedule_id: str,
    principal: Principal = Depends(require("inference.schedule.update")),
):
    c = _c(request)
    sch = await c.schedules.resume(principal.ctx(request.state.trace_id), schedule_id)
    return {"data": schedule_payload(sch)}


@router.post("/schedules/{schedule_id}/trigger", status_code=202)
async def trigger_schedule(
    request: Request, schedule_id: str,
    principal: Principal = Depends(require("inference.schedule.update")),
):
    c = _c(request)
    result = await c.schedules.trigger(principal.ctx(request.state.trace_id), schedule_id)
    return {"data": result}


@router.get("/schedules/{schedule_id}/fires")
async def schedule_fires(
    request: Request, schedule_id: str,
    principal: Principal = Depends(require("inference.schedule.read")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)
    await c.schedules.get(ctx, schedule_id)  # 404 if missing/cross-tenant
    from app.api.schemas import job_payload

    page = await c.inference.list(ctx, Filters(schedule_id=schedule_id), "-created_at",
                                  limit, cursor)
    return page_envelope([job_payload(j) for j in page.items], page.next_cursor, page.has_more)
