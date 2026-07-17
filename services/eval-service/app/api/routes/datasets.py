from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import Principal, require
from app.api.schemas import DatasetCreate, data
from app.api.serialize import dump, dump_page

router = APIRouter(prefix="/api/v1")


def _ctx(request: Request, principal: Principal):
    return principal.ctx(getattr(request.state, "trace_id", None))


@router.post("/datasets", status_code=201)
async def create_dataset(
    request: Request,
    body: DatasetCreate,
    principal: Principal = Depends(require("eval.dataset.write")),
):
    svc = request.app.state.container.dataset_service
    d = await svc.create(_ctx(request, principal), body.model_dump())
    return data(dump(d))


@router.get("/datasets")
async def list_datasets(
    request: Request,
    agent_key: str | None = None,
    limit: int = Query(50, le=200),
    cursor: str | None = None,
    principal: Principal = Depends(require("eval.dataset.read")),
):
    svc = request.app.state.container.dataset_service
    page = await svc.list(_ctx(request, principal), agent_key, limit, cursor)
    return dump_page(page)


@router.get("/datasets/{dataset_key:path}/versions/{version}")
async def get_dataset(
    request: Request,
    dataset_key: str,
    version: int,
    principal: Principal = Depends(require("eval.dataset.read")),
):
    svc = request.app.state.container.dataset_service
    d = await svc.get(_ctx(request, principal), dataset_key, version)
    return data(dump(d))


@router.post("/datasets/{dataset_key:path}/versions/{version}/freeze")
async def freeze_dataset(
    request: Request,
    dataset_key: str,
    version: int,
    principal: Principal = Depends(require("eval.dataset.write")),
):
    svc = request.app.state.container.dataset_service
    d = await svc.freeze(_ctx(request, principal), dataset_key, version)
    return data(dump(d))
