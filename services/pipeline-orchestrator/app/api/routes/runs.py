"""Run lifecycle read + control (BRD §5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import Principal, require
from app.api.schemas import page_envelope, run_payload
from app.domain.ports import RunFilters

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.get("/runs")
async def list_runs(request: Request,
                    principal: Principal = Depends(require("pipeline.run.read")),
                    limit: int = Query(default=50, ge=1, le=200),
                    cursor: str | None = None,
                    status: str | None = Query(default=None, alias="filter[status]"),
                    template_id: str | None = Query(
                        default=None, alias="filter[template_id]")):
    c = _c(request)
    filters = RunFilters(status=status, template_id=template_id)
    page = await c.run_service.list(principal.ctx(request.state.trace_id), filters,
                                    limit, cursor)
    return page_envelope([run_payload(r) for r in page.items], page.next_cursor,
                         page.has_more)


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str,
                  principal: Principal = Depends(require("pipeline.run.read"))):
    c = _c(request)
    run = await c.run_service.get(principal.ctx(request.state.trace_id), run_id)
    return {"data": run_payload(run)}


@router.put("/runs/{run_id}/terminate")
async def terminate(request: Request, run_id: str,
                    principal: Principal = Depends(require("pipeline.run.execute"))):
    c = _c(request)
    run = await c.run_service.terminate(principal.ctx(request.state.trace_id), run_id)
    return {"data": run_payload(run)}


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry(request: Request, run_id: str,
                principal: Principal = Depends(require("pipeline.run.create"))):
    c = _c(request)
    from app.domain.enums import RunStatus

    operation_id, run = await c.run_service.retry(
        principal.ctx(request.state.trace_id), run_id)
    if run.status == int(RunStatus.submitted):
        c.schedule_drive(run.tenant_id, run.id)
    return {"operation_id": operation_id, "data": run_payload(run)}


@router.get("/runs/{run_id}/manifest")
async def manifest(request: Request, run_id: str,
                   principal: Principal = Depends(require("pipeline.run.read"))):
    c = _c(request)
    run, manifest_doc, resolved = await c.run_service.get_manifest(
        principal.ctx(request.state.trace_id), run_id)
    return {"data": {"run_id": run.id, "manifest": manifest_doc,
                     "resolved_parameters": resolved}}
