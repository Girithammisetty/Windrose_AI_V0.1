"""Compile endpoints (SEM-FR-020..026, BR-1/BR-2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi import Body as FBody

from app.api.auth import Principal, get_bearer_token, require
from app.domain.errors import PermissionDenied, ValidationFailed

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


async def _draft_version(request: Request, principal: Principal) -> int | None:
    """BR-2: drafts compile only with X-Draft-Version + semantic.model.update."""
    header = request.headers.get("x-draft-version")
    if header is None:
        return None
    try:
        version_no = int(header)
    except ValueError as exc:
        raise ValidationFailed("X-Draft-Version must be an integer") from exc
    authz = request.app.state.authz
    if not await authz.allow(principal, "semantic.model.update", None):
        raise PermissionDenied("draft compiles require semantic.model.update")
    return version_no


@router.post("/compile")
async def compile_metrics(
    request: Request,
    body: dict = FBody(...),
    validate: bool = False,
    principal: Principal = Depends(require("semantic.compile.execute")),
):
    c = _c(request)
    draft_version = await _draft_version(request, principal)
    result = await c.compile_service.compile(
        principal.ctx(request.state.trace_id), body,
        caller_class="agent_tool" if principal.is_agent else "api",
        draft_version_no=draft_version, validate=validate,
        token=get_bearer_token(request))
    return {"data": result}


@router.post("/compile/chart")
async def compile_chart(
    request: Request,
    body: dict = FBody(...),
    validate: bool = False,
    principal: Principal = Depends(require("semantic.compile.execute")),
):
    c = _c(request)
    result = await c.compile_service.compile_chart(
        principal.ctx(request.state.trace_id), body, validate=validate,
        token=get_bearer_token(request))
    return {"data": result}
