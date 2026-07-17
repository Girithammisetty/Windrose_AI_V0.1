"""Internal endpoints: component error-report (SPIFFE mTLS, PIPE-FR-036),
platform-admin quota management (PIPE-FR-040), and the MCP backend facade
tool-plane federates write-proposal tool execution to (TPL-FR-012)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.auth import Principal, require, require_internal
from app.api.schemas import QuotaUpdate
from app.domain.entities import CallCtx
from app.domain.errors import AppError

router = APIRouter()


class ComponentError(BaseModel):
    tenant_id: str
    title: str
    detail: str
    source: str | None = None
    alias: str | None = None


@router.post("/internal/runs/{argo_workflow_name}/error")
async def report_error(request: Request, argo_workflow_name: str, body: ComponentError,
                       spiffe: str = Depends(require_internal)):
    """Components report structured exceptions for UI display (stored on the run)."""
    c = request.app.state.container
    stored = await c.run_service.record_component_error(
        body.tenant_id, argo_workflow_name, body.model_dump())
    return {"data": {"recorded": stored}}


class McpInvokeRequest(BaseModel):
    tool_id: str
    version: str | None = None
    args: dict = {}
    tenant: str
    obo_sub: str | None = None
    agent_id: str | None = None


# tool_id -> the action a caller must hold (mirrors the REST endpoint that
# exposes the same capability to humans — an agent's proposal-execution grant
# is not a permission bypass, just a durable record of a human's APPROVE).
_MCP_TOOL_ACTIONS = {
    "pipeline.template.create_from_algorithm": "pipeline.template.create",
}


def _mcp_output(status: int, output: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content={"output": output})


@router.post("/internal/v1/mcp/invoke")
async def mcp_invoke(request: Request, body: McpInvokeRequest,
                     spiffe: str = Depends(require_internal)):
    """The real MCP backend facade tool-plane federates write-proposal tool
    execution to (TPL-FR-012, master §2.2-015). Reached only after tool-plane's
    full enforcement pipeline (OPA + a signed proposal-execution grant bound to
    this exact tenant/tool/tier/args digest) — this handler still re-checks
    authorization for the EFFECTIVE HUMAN (obo_sub) against the real OPA
    sidecar, the same defense-in-depth case-service's facade applies: the
    backend never blindly trusts the gateway."""
    c = request.app.state.container
    action = _MCP_TOOL_ACTIONS.get(body.tool_id)
    if action is None:
        return _mcp_output(404, {"error": f"unknown tool_id {body.tool_id!r}"})

    principal = Principal(
        sub=f"agent:{body.agent_id}" if body.agent_id else "svc:mcp-gateway",
        tenant_id=body.tenant, typ="agent_obo" if body.agent_id else "service",
        agent_id=body.agent_id, obo_sub=body.obo_sub,
        workspace_id=body.args.get("workspace_id"))
    if not await request.app.state.authz.allow(principal, action, None):
        return _mcp_output(403, {"error": f"not allowed: {action}"})

    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    try:
        if body.tool_id == "pipeline.template.create_from_algorithm":
            args = body.args
            out = await c.mcp.template_create_from_algorithm(
                ctx, algorithm=args["algorithm"], mode=args.get("mode", "train"),
                dataset_refs=args["dataset_refs"], params=args.get("params", {}),
                workspace_id=args.get("workspace_id"), name=args.get("name"))
        else:
            return _mcp_output(404, {"error": f"unknown tool_id {body.tool_id!r}"})
    except AppError as exc:
        return _mcp_output(exc.status, {"error": exc.message, "code": exc.code})
    except KeyError as exc:
        return _mcp_output(422, {"error": f"missing required arg {exc}"})
    return {"output": out}


api = APIRouter(prefix="/api/v1")


@api.get("/admin/quotas/{tenant_id}")
async def get_quota(request: Request, tenant_id: str,
                    principal: Principal = Depends(require("pipeline.quota.admin"))):
    c = request.app.state.container
    quota = await c.admin_service.get_quota(
        CallCtx(tenant_id=tenant_id, actor=principal.actor), tenant_id)
    return {"data": _quota_payload(quota) if quota else None}


@api.put("/admin/quotas/{tenant_id}")
async def set_quota(request: Request, tenant_id: str, body: QuotaUpdate,
                    principal: Principal = Depends(require("pipeline.quota.admin"))):
    c = request.app.state.container
    quota = await c.admin_service.set_quota(
        CallCtx(tenant_id=tenant_id, actor=principal.actor), tenant_id,
        body.model_dump(exclude_none=True))
    return {"data": _quota_payload(quota)}


def _quota_payload(q) -> dict:
    return {"tenant_id": q.tenant_id, "max_concurrent_runs": q.max_concurrent_runs,
            "max_concurrent_pods": q.max_concurrent_pods,
            "max_run_duration_minutes": q.max_run_duration_minutes,
            "min_seconds_between_runs": q.min_seconds_between_runs,
            "resource_ceiling": q.resource_ceiling, "node_pool": q.node_pool}
