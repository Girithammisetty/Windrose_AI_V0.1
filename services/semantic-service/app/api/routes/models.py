"""Model CRUD, versioning, review workflow, definition, bootstrap (BRD §5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.api.idempotency import idempotent
from app.api.schemas import (
    BootstrapBody,
    DecisionBody,
    DefinitionPatch,
    ModelCreate,
    ModelPatch,
    page_envelope,
)
from app.domain.services import model_payload
from app.utils import sha256_hex

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


def _version_view(v, include_definition: bool = True) -> dict:
    view = {
        "id": v.id, "model_id": v.model_id, "version_no": v.version_no,
        "status": v.status, "diff": v.diff, "submitted_by": v.submitted_by,
        "approved_by": v.approved_by, "decision_note": v.decision_note,
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "created_at": v.created_at.isoformat(),
    }
    if include_definition:
        view["definition"] = v.definition
    return view


@router.post("/models", status_code=201)
async def create_model(
    request: Request,
    response: Response,
    body: ModelCreate,
    principal: Principal = Depends(require("semantic.model.create")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        model, version = await c.model_service.create(ctx, body.model_dump())
        payload = model_payload(model)
        payload["draft_version"] = _version_view(version, include_definition=False)
        return 201, {"data": payload}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.get("/models")
async def list_models(
    request: Request,
    principal: Principal = Depends(require("semantic.model.read")),
    workspace_id: str | None = Query(default=None, alias="filter[workspace_id]"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    page = await c.model_service.list(
        principal.ctx(request.state.trace_id), workspace_id, limit, cursor)
    return page_envelope([model_payload(m) for m in page.items],
                         page.next_cursor, page.has_more)


@router.get("/models/{model_id}")
async def get_model(
    request: Request,
    model_id: str,
    principal: Principal = Depends(require("semantic.model.read")),
):
    c = _c(request)
    model, published_no = await c.model_service.get(
        principal.ctx(request.state.trace_id), model_id)
    return {"data": model_payload(model, published_no)}


@router.patch("/models/{model_id}")
async def patch_model(
    request: Request,
    model_id: str,
    body: ModelPatch,
    principal: Principal = Depends(require("semantic.model.update")),
):
    c = _c(request)
    model = await c.model_service.patch(
        principal.ctx(request.state.trace_id), model_id,
        body.model_dump(exclude_unset=True))
    return {"data": model_payload(model)}


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    request: Request,
    model_id: str,
    principal: Principal = Depends(require("semantic.model.delete")),
):
    c = _c(request)
    await c.model_service.delete(principal.ctx(request.state.trace_id), model_id)
    return Response(status_code=204)


# -- versions -----------------------------------------------------------------


@router.get("/models/{model_id}/versions")
async def list_versions(
    request: Request,
    model_id: str,
    principal: Principal = Depends(require("semantic.model.read")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
):
    c = _c(request)
    page = await c.version_service.list(
        principal.ctx(request.state.trace_id), model_id, limit, cursor)
    return page_envelope([_version_view(v, include_definition=False)
                          for v in page.items], page.next_cursor, page.has_more)


@router.post("/models/{model_id}/versions", status_code=201)
async def create_version(
    request: Request,
    response: Response,
    model_id: str,
    principal: Principal = Depends(require("semantic.model.update")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        version = await c.version_service.create_draft(ctx, model_id)
        return 201, {"data": _version_view(version)}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.get("/models/{model_id}/versions/{version_no}")
async def get_version(
    request: Request,
    model_id: str,
    version_no: int,
    principal: Principal = Depends(require("semantic.model.read")),
):
    c = _c(request)
    version = await c.version_service.get(
        principal.ctx(request.state.trace_id), model_id, version_no)
    return {"data": _version_view(version)}


@router.patch("/models/{model_id}/versions/{version_no}")
async def patch_version(
    request: Request,
    model_id: str,
    version_no: int,
    body: DefinitionPatch,
    principal: Principal = Depends(require("semantic.model.update")),
):
    c = _c(request)
    version = await c.version_service.patch_draft(
        principal.ctx(request.state.trace_id), model_id, version_no, body.definition)
    return {"data": _version_view(version)}


@router.post("/models/{model_id}/versions/{version_no}/submit")
async def submit_version(
    request: Request,
    model_id: str,
    version_no: int,
    principal: Principal = Depends(require("semantic.model.update")),
):
    c = _c(request)
    version = await c.version_service.submit(
        principal.ctx(request.state.trace_id), model_id, version_no)
    return {"data": _version_view(version, include_definition=False)}


@router.post("/models/{model_id}/versions/{version_no}/approve")
async def approve_version(
    request: Request,
    model_id: str,
    version_no: int,
    body: DecisionBody | None = None,
    principal: Principal = Depends(require("semantic.model.approve")),
):
    c = _c(request)
    version = await c.version_service.approve(
        principal.ctx(request.state.trace_id), model_id, version_no,
        body.note if body else None)
    return {"data": _version_view(version, include_definition=False)}


@router.post("/models/{model_id}/versions/{version_no}/reject")
async def reject_version(
    request: Request,
    model_id: str,
    version_no: int,
    body: DecisionBody,
    principal: Principal = Depends(require("semantic.model.approve")),
):
    c = _c(request)
    version = await c.version_service.reject(
        principal.ctx(request.state.trace_id), model_id, version_no, body.note)
    return {"data": _version_view(version, include_definition=False)}


@router.get("/models/{model_id}/definition")
async def get_definition(
    request: Request,
    response: Response,
    model_id: str,
    version: int | None = None,
    principal: Principal = Depends(require("semantic.model.read")),
):
    c = _c(request)
    definition, version_no = await c.version_service.get_definition(
        principal.ctx(request.state.trace_id), model_id, version)
    etag = sha256_hex(f"{model_id}:{version_no}")[:16]
    response.headers["ETag"] = f'"{etag}"'
    return {"data": {"version_no": version_no, "definition": definition}}


# -- bootstrap (SEM-FR-060) -----------------------------------------------------


@router.post("/models/{model_id}/bootstrap", status_code=202)
async def bootstrap_model(
    request: Request,
    response: Response,
    model_id: str,
    body: BootstrapBody,
    principal: Principal = Depends(require("semantic.model.update")),
):
    c = _c(request)
    ctx = principal.ctx(request.state.trace_id)

    async def work():
        operation = await c.bootstrap_service.run(ctx, model_id, body.sources)
        return 202, {"data": {"operation_id": operation.id, "status": operation.status,
                              "report": operation.report}}

    return await idempotent(request, response, c.deps.uow_factory, ctx.tenant_id, work)


@router.get("/operations/{operation_id}")
async def get_operation(
    request: Request,
    operation_id: str,
    principal: Principal = Depends(require("semantic.model.read")),
):
    c = _c(request)
    op = await c.bootstrap_service.get_operation(
        principal.ctx(request.state.trace_id), operation_id)
    return {"data": {"operation_id": op.id, "kind": op.kind, "status": op.status,
                     "report": op.report,
                     "created_at": op.created_at.isoformat(),
                     "finished_at": op.finished_at.isoformat() if op.finished_at
                     else None}}
