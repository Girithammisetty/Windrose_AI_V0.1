from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import Principal, require
from app.api.schemas import AttestBody, CaseCreate, CasePatch, data
from app.api.serialize import dump, dump_page

router = APIRouter(prefix="/api/v1")


def _ctx(request, principal):
    return principal.ctx(getattr(request.state, "trace_id", None))


@router.post("/cases", status_code=201)
async def create_case(
    request: Request, body: CaseCreate, principal: Principal = Depends(require("eval.case.curate"))
):
    svc = request.app.state.container.case_service
    c = await svc.create(_ctx(request, principal), body.model_dump())
    return data(dump(c))


@router.get("/cases")
async def list_cases(
    request: Request,
    status: str = Query("candidate", alias="filter[status]"),
    dataset_key: str | None = Query(None, alias="filter[dataset_key]"),
    dataset_version: int | None = Query(None, alias="filter[dataset_version]"),
    source: str | None = Query(None, alias="filter[source]"),
    limit: int = Query(50, le=200),
    cursor: str | None = None,
    principal: Principal = Depends(require("eval.case.read")),
):
    svc = request.app.state.container.case_service
    page = await svc.list_queue(
        _ctx(request, principal), dataset_key, dataset_version, status, source, None, limit, cursor
    )
    return dump_page(page)


@router.get("/cases/{case_id}")
async def get_case(
    request: Request, case_id: str, principal: Principal = Depends(require("eval.case.read"))
):
    svc = request.app.state.container.case_service
    return data(dump(await svc.get(_ctx(request, principal), case_id)))


@router.post("/cases/{case_id}/promote")
async def promote_case(
    request: Request, case_id: str, principal: Principal = Depends(require("eval.case.curate"))
):
    svc = request.app.state.container.case_service
    return data(dump(await svc.promote(_ctx(request, principal), case_id)))


@router.post("/cases/{case_id}/attest")
async def attest_case(
    request: Request,
    case_id: str,
    body: AttestBody,
    principal: Principal = Depends(require("eval.case.curate")),
):
    svc = request.app.state.container.case_service
    return data(dump(await svc.attest(_ctx(request, principal), case_id, body.attested_by)))


@router.post("/cases/{case_id}/reject")
async def reject_case(
    request: Request, case_id: str, principal: Principal = Depends(require("eval.case.curate"))
):
    svc = request.app.state.container.case_service
    return data(dump(await svc.reject(_ctx(request, principal), case_id)))


@router.post("/cases/{case_id}/retire")
async def retire_case(
    request: Request, case_id: str, principal: Principal = Depends(require("eval.case.curate"))
):
    svc = request.app.state.container.case_service
    return data(dump(await svc.retire(_ctx(request, principal), case_id)))


@router.patch("/cases/{case_id}")
async def patch_case(
    request: Request,
    case_id: str,
    body: CasePatch,
    principal: Principal = Depends(require("eval.case.curate")),
):
    svc = request.app.state.container.case_service
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return data(dump(await svc.edit(_ctx(request, principal), case_id, patch)))
