"""MCP-facing read tools as REST endpoints with JSON-Schema I/O (SEM-FR-080).

The real MCP server wrapper is stubbed (app/mcp/facade.py::McpServer TODO);
tool-plane registration consumes GET /api/v1/tools.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi import Body as FBody

from app.api.auth import Principal, get_bearer_token, require
from app.mcp.facade import TOOL_SCHEMAS

router = APIRouter(prefix="/api/v1/tools")


def _mcp(request: Request):
    return request.app.state.container.mcp


@router.get("")
async def list_tools(
    request: Request,
    principal: Principal = Depends(require("semantic.model.read")),
):
    return {"data": [
        {"name": name, "version": "1.0.0", **schema}
        for name, schema in TOOL_SCHEMAS.items()
    ]}


@router.post("/get_metrics")
async def get_metrics(
    request: Request,
    body: dict = FBody(default={}),
    principal: Principal = Depends(require("semantic.model.read")),
):
    result = await _mcp(request).get_metrics(
        principal.ctx(request.state.trace_id),
        body.get("model"), body.get("workspace_id"))
    return {"data": result}


@router.post("/get_dimensions")
async def get_dimensions(
    request: Request,
    body: dict = FBody(default={}),
    principal: Principal = Depends(require("semantic.model.read")),
):
    result = await _mcp(request).get_dimensions(
        principal.ctx(request.state.trace_id),
        body.get("model"), body.get("metric"), body.get("workspace_id"))
    return {"data": result}


@router.post("/compile_metric_sql")
async def compile_metric_sql(
    request: Request,
    body: dict = FBody(...),
    principal: Principal = Depends(require("semantic.compile.execute")),
):
    result = await _mcp(request).compile_metric_sql(
        principal.ctx(request.state.trace_id), body,
        token=get_bearer_token(request))
    return {"data": result}


@router.post("/search_verified_queries")
async def search_verified_queries(
    request: Request,
    body: dict = FBody(...),
    principal: Principal = Depends(require("semantic.verified_query.read")),
):
    result = await _mcp(request).search_verified_queries(
        principal.ctx(request.state.trace_id),
        body.get("workspace_id") or "", body.get("q") or "",
        int(body.get("top_k") or 5))
    return {"data": result}
