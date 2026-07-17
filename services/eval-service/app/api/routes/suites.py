from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import Principal, require
from app.api.schemas import SuiteCreate, SuitePatch, data
from app.api.serialize import dump

router = APIRouter(prefix="/api/v1")


def _ctx(request, principal):
    return principal.ctx(getattr(request.state, "trace_id", None))


@router.post("/suites", status_code=201)
async def create_suite(
    request: Request, body: SuiteCreate, principal: Principal = Depends(require("eval.suite.write"))
):
    svc = request.app.state.container.suite_service
    return data(dump(await svc.create(_ctx(request, principal), body.model_dump())))


@router.get("/suites/{suite_id}")
async def get_suite(
    request: Request,
    suite_id: str,
    version: int | None = None,
    principal: Principal = Depends(require("eval.suite.write")),
):
    svc = request.app.state.container.suite_service
    return data(dump(await svc.get(_ctx(request, principal), suite_id, version)))


@router.patch("/suites/{suite_id}")
async def patch_suite(
    request: Request,
    suite_id: str,
    body: SuitePatch,
    version: int | None = None,
    principal: Principal = Depends(require("eval.suite.write")),
):
    svc = request.app.state.container.suite_service
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return data(dump(await svc.update(_ctx(request, principal), suite_id, patch, version)))
