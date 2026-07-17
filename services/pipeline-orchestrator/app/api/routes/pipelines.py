"""Pipeline template CRUD, validation, compilation, run submission (BRD §5)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.api.auth import Principal, require
from app.api.schemas import (
    RunRequest,
    TemplateCreate,
    TemplateUpdate,
    ValidateRequest,
    page_envelope,
    run_payload,
    template_payload,
    version_payload,
)
from app.domain.enums import RunStatus
from app.domain.ports import TemplateFilters

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.post("/pipelines/validate")
async def validate(request: Request, body: ValidateRequest,
                   mode: str = Query(default="structure_only"),
                   principal: Principal = Depends(require("pipeline.template.create"))):
    c = _c(request)
    report = await c.template_service.validate(
        principal.ctx(request.state.trace_id), body.definition,
        pipeline_type=body.pipeline_type, model_type=body.model_type,
        mode="all" if mode == "all" else ("provided_parameters"
                                          if mode == "provided_parameters" else "all"))
    status = 200 if report.valid else 422
    return Response(status_code=status, media_type="application/json",
                    content=_json({"data": report.to_dict()}))


@router.post("/pipelines", status_code=201)
async def create(request: Request, body: TemplateCreate,
                 principal: Principal = Depends(require("pipeline.template.create"))):
    c = _c(request)
    template, version = await c.template_service.create(
        principal.ctx(request.state.trace_id), body.model_dump())
    return {"data": template_payload(template, version)}


@router.put("/pipelines/{template_id}")
async def update(request: Request, template_id: str, body: TemplateUpdate,
                 if_match: str | None = Header(default=None, alias="If-Match"),
                 principal: Principal = Depends(require("pipeline.template.update"))):
    c = _c(request)
    template, version = await c.template_service.update(
        principal.ctx(request.state.trace_id), template_id,
        body.model_dump(exclude_none=True), if_match=if_match)
    return {"data": template_payload(template, version)}


@router.get("/pipelines")
async def list_templates(request: Request,
                         principal: Principal = Depends(require("pipeline.template.read")),
                         limit: int = Query(default=50, ge=1, le=200),
                         cursor: str | None = None,
                         name: str | None = Query(default=None, alias="filter[name]"),
                         pipeline_type: str | None = Query(
                             default=None, alias="filter[pipeline_type]"),
                         include_archived: bool = False):
    c = _c(request)
    filters = TemplateFilters(name=name, pipeline_type=pipeline_type,
                              include_archived=include_archived)
    page = await c.template_service.list(principal.ctx(request.state.trace_id), filters,
                                         limit, cursor)
    return page_envelope([template_payload(t) for t in page.items], page.next_cursor,
                         page.has_more)


@router.get("/pipelines/{template_id}")
async def get_template(request: Request, template_id: str,
                       principal: Principal = Depends(require("pipeline.template.read"))):
    c = _c(request)
    template, version = await c.template_service.get(
        principal.ctx(request.state.trace_id), template_id)
    return {"data": template_payload(template, version)}


@router.get("/pipelines/{template_id}/versions")
async def list_versions(request: Request, template_id: str,
                        principal: Principal = Depends(require("pipeline.template.read")),
                        limit: int = Query(default=50, ge=1, le=200),
                        cursor: str | None = None):
    c = _c(request)
    page = await c.template_service.versions(principal.ctx(request.state.trace_id),
                                             template_id, limit, cursor)
    return page_envelope([version_payload(v) for v in page.items], page.next_cursor,
                         page.has_more)


@router.post("/pipelines/{template_id}/versions/{version_id}/activate")
async def activate(request: Request, template_id: str, version_id: str,
                   principal: Principal = Depends(require("pipeline.template.update"))):
    c = _c(request)
    template, version = await c.template_service.activate_version(
        principal.ctx(request.state.trace_id), template_id, version_id)
    return {"data": template_payload(template, version)}


@router.delete("/pipelines/{template_id}")
async def archive(request: Request, template_id: str,
                  principal: Principal = Depends(require("pipeline.template.delete"))):
    c = _c(request)
    template = await c.template_service.archive(principal.ctx(request.state.trace_id),
                                                template_id)
    return {"data": template_payload(template)}


@router.patch("/pipelines/{template_id}/restore")
async def restore(request: Request, template_id: str,
                  principal: Principal = Depends(require("pipeline.template.update"))):
    c = _c(request)
    template = await c.template_service.restore(principal.ctx(request.state.trace_id),
                                                template_id)
    return {"data": template_payload(template)}


@router.post("/pipelines/{template_id}/clone", status_code=201)
async def clone(request: Request, template_id: str,
                principal: Principal = Depends(require("pipeline.template.create"))):
    c = _c(request)
    template, version = await c.template_service.clone(
        principal.ctx(request.state.trace_id), template_id)
    return {"data": template_payload(template, version)}


@router.post("/pipelines/{template_id}/compile")
async def compile_template(request: Request, template_id: str,
                           principal: Principal = Depends(
                               require("pipeline.template.execute"))):
    c = _c(request)
    template, version, manifest = await c.template_service.compile(
        principal.ctx(request.state.trace_id), template_id)
    return {"data": {"template_id": template.id, "version_id": version.id,
                     "manifest_digest": version.manifest_digest,
                     "argo_template_name": version.argo_template_name,
                     "manifest": manifest}}


@router.post("/pipelines/{template_id}/run", status_code=202)
async def run(request: Request, template_id: str, body: RunRequest,
              principal: Principal = Depends(require("pipeline.run.create"))):
    c = _c(request)
    operation_id, run_obj = await c.run_service.create_run(
        principal.ctx(request.state.trace_id), template_id, body.run_parameters)
    if run_obj.status == int(RunStatus.submitted):
        c.schedule_drive(run_obj.tenant_id, run_obj.id)
    return {"operation_id": operation_id, "data": run_payload(run_obj)}


def _json(obj) -> str:
    import json

    return json.dumps(obj, default=str)


_ = asyncio
