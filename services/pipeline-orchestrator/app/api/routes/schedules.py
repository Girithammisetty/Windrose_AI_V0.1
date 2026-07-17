"""Recurring pipeline schedule CRUD + control (PIPE-FR-050).

Auth + envelope mirror the pipelines routes: each endpoint is guarded with
``require(...)`` and returns the ``{"data": ...}`` envelope. run-now drives the
created run through the same fire-and-forget executor path the /run route uses."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth import Principal, require
from app.api.schemas import ScheduleCreate, page_envelope, run_payload, schedule_payload
from app.domain.enums import RunStatus

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.post("/pipeline-schedules", status_code=201)
async def create_schedule(request: Request, body: ScheduleCreate,
                          principal: Principal = Depends(
                              require("pipeline.schedule.create"))):
    c = _c(request)
    sched = await c.schedule_service.create(
        principal.ctx(request.state.trace_id), body.template_id, body.name,
        body.cron, body.timezone, body.run_parameters)
    return {"data": schedule_payload(sched)}


@router.get("/pipeline-schedules")
async def list_schedules(request: Request,
                         principal: Principal = Depends(
                             require("pipeline.schedule.read"))):
    c = _c(request)
    items = await c.schedule_service.list(principal.ctx(request.state.trace_id))
    return page_envelope([schedule_payload(s) for s in items], None, False)


@router.get("/pipeline-schedules/{schedule_id}")
async def get_schedule(request: Request, schedule_id: str,
                       principal: Principal = Depends(
                           require("pipeline.schedule.read"))):
    c = _c(request)
    sched = await c.schedule_service.get(principal.ctx(request.state.trace_id),
                                         schedule_id)
    return {"data": schedule_payload(sched)}


@router.post("/pipeline-schedules/{schedule_id}/pause")
async def pause_schedule(request: Request, schedule_id: str,
                         principal: Principal = Depends(
                             require("pipeline.schedule.update"))):
    c = _c(request)
    sched = await c.schedule_service.pause(principal.ctx(request.state.trace_id),
                                           schedule_id)
    return {"data": schedule_payload(sched)}


@router.post("/pipeline-schedules/{schedule_id}/resume")
async def resume_schedule(request: Request, schedule_id: str,
                          principal: Principal = Depends(
                              require("pipeline.schedule.update"))):
    c = _c(request)
    sched = await c.schedule_service.resume(principal.ctx(request.state.trace_id),
                                            schedule_id)
    return {"data": schedule_payload(sched)}


@router.post("/pipeline-schedules/{schedule_id}/run-now", status_code=202)
async def run_now(request: Request, schedule_id: str,
                  principal: Principal = Depends(
                      require("pipeline.schedule.execute"))):
    c = _c(request)
    sched, run = await c.schedule_service.run_now(
        principal.ctx(request.state.trace_id), schedule_id)
    if run is not None and run.status == int(RunStatus.submitted):
        c.schedule_drive(run.tenant_id, run.id)
    return {"data": schedule_payload(sched),
            "run": run_payload(run) if run is not None else None}


@router.delete("/pipeline-schedules/{schedule_id}", status_code=204)
async def delete_schedule(request: Request, schedule_id: str,
                          principal: Principal = Depends(
                              require("pipeline.schedule.delete"))):
    c = _c(request)
    await c.schedule_service.delete(principal.ctx(request.state.trace_id), schedule_id)
    return Response(status_code=204)
