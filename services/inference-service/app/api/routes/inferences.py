"""Batch inference job endpoints (BRD §5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.api.idempotency import idempotent
from app.api.schemas import (
    BulkBody,
    SubmitBody,
    ValidateBody,
    job_payload,
    page_envelope,
)
from app.domain.enums import JobStatus
from app.domain.ports import Filters
from app.domain.services import SubmitRequest

router = APIRouter(prefix="/api/v1")

_SORTS = {"-created_at", "created_at"}


def _c(request: Request):
    return request.app.state.container


def _submit_request(body: SubmitBody) -> SubmitRequest:
    out = None
    if body.output is not None:
        out = {"dataset_name": body.output.dataset_name}
        if body.output.mode:
            out["mode"] = body.output.mode
    return SubmitRequest(
        model_version_urn=body.model_version_urn,
        input_dataset_urn=body.input_dataset_urn,
        name=body.name, description=body.description, parameters=body.parameters,
        output=out, allow_unpromoted=body.allow_unpromoted, allow_empty=body.allow_empty,
    )


@router.post("/inferences", status_code=202)
async def submit_inference(
    request: Request, response: Response, body: SubmitBody,
    principal: Principal = Depends(require("inference.job.create")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        job = await c.inference.submit(ctx, _submit_request(body))
        status = 202 if job.status != int(JobStatus.rejected) else 422
        payload = {"operation_id": job.id, "job_id": job.id, "status": job_payload(job)["status"]}
        if job.status == int(JobStatus.rejected):
            payload = {"error": job.error}
        return status, {"data": payload} if status == 202 else payload

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.post("/inferences/validate")
async def validate_inference(
    request: Request, body: ValidateBody,
    principal: Principal = Depends(require("inference.job.read")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)
    req = SubmitRequest(
        model_version_urn=body.model_version_urn, input_dataset_urn=body.input_dataset_urn,
        allow_unpromoted=body.allow_unpromoted, allow_empty=body.allow_empty)
    report = await c.inference.validate(ctx, req)
    return {"data": report}


@router.get("/inferences")
async def list_inferences(
    request: Request,
    principal: Principal = Depends(require("inference.job.read")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    sort: str = "-created_at",
    status: str | None = Query(default=None, alias="filter[status]"),
    model_version_urn: str | None = Query(default=None, alias="filter[model_version_urn]"),
    schedule_id: str | None = Query(default=None, alias="filter[schedule_id]"),
):
    c = _c(request)
    sort = sort if sort in _SORTS else "-created_at"
    status_val = int(JobStatus[status]) if status and status in JobStatus.__members__ else None
    filters = Filters(status=status_val, model_version_urn=model_version_urn,
                      schedule_id=schedule_id)
    page = await c.inference.list(principal.ctx(request.state.trace_id), filters, sort,
                                  limit, cursor)
    return page_envelope([job_payload(j) for j in page.items], page.next_cursor, page.has_more)


@router.get("/inferences/{job_id}")
async def get_inference(
    request: Request, job_id: str,
    principal: Principal = Depends(require("inference.job.read")),
):
    c = _c(request)
    job = await c.inference.get(principal.ctx(request.state.trace_id), job_id)
    return {"data": job_payload(job)}


@router.post("/inferences/{job_id}/cancel")
async def cancel_inference(
    request: Request, job_id: str,
    principal: Principal = Depends(require("inference.job.update")),
):
    c = _c(request)
    job = await c.inference.cancel(principal.ctx(request.state.trace_id), job_id)
    return {"data": job_payload(job)}


@router.post("/inferences/{job_id}/retry", status_code=202)
async def retry_inference(
    request: Request, job_id: str,
    principal: Principal = Depends(require("inference.job.create")),
):
    c = _c(request)
    job = await c.inference.retry(principal.ctx(request.state.trace_id), job_id)
    return {"data": {"operation_id": job.id, "job_id": job.id}}


@router.delete("/inferences/{job_id}", status_code=204)
async def delete_inference(
    request: Request, job_id: str,
    principal: Principal = Depends(require("inference.job.delete")),
):
    c = _c(request)
    await c.inference.delete(principal.ctx(request.state.trace_id), job_id)
    return Response(status_code=204)


@router.post("/inferences/bulk")
async def bulk_inference(
    request: Request, body: BulkBody,
    principal: Principal = Depends(require("inference.job.create")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)
    out = None
    if body.output is not None:
        out = {"dataset_name": body.output.dataset_name, "mode": body.output.mode}
    results = await c.inference.bulk(ctx, body.model_version_urn, body.input_dataset_urns,
                                     {"parameters": body.parameters, "output": out})
    return {"data": results}
