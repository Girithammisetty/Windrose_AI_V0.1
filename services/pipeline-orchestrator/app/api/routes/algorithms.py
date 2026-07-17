"""Algorithm-template catalog + instantiation (PIPE-FR-052)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import Principal, require
from app.api.schemas import (
    InstantiateRequest,
    algorithm_payload,
    template_payload,
)

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.get("/algorithm-templates")
async def list_algorithms(request: Request,
                          principal: Principal = Depends(
                              require("pipeline.algorithm.read"))):
    c = _c(request)
    return {"data": [algorithm_payload(a) for a in c.catalog_service.list_algorithms()]}


@router.get("/algorithm-templates/{name}")
async def get_algorithm(request: Request, name: str,
                        principal: Principal = Depends(
                            require("pipeline.algorithm.read"))):
    c = _c(request)
    return {"data": algorithm_payload(c.catalog_service.get_algorithm(name))}


@router.post("/algorithm-templates/{name}/pipelines", status_code=201)
async def instantiate(request: Request, name: str, body: InstantiateRequest,
                      principal: Principal = Depends(
                          require("pipeline.template.create"))):
    c = _c(request)
    template, version = await c.instantiation_service.instantiate_pipeline(
        principal.ctx(request.state.trace_id), name, mode=body.mode,
        dataset_refs=body.dataset_refs, params=body.parameters,
        workspace_id=body.workspace_id, name=body.name)
    return {"data": template_payload(template, version)}
