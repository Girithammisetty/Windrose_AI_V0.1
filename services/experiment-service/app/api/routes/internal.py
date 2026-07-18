"""Internal endpoints: MLflow webhook ingest (HMAC) + manual reconciliation.

Auth: the mesh sidecar injects the SPIFFE identity after mTLS; the webhook
additionally verifies a shared HMAC body signature and dedups on the delivery id
(EXP-FR-010/011). Never called by external clients.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.auth import Principal, require_internal
from app.api.errors import error_response
from app.domain.errors import AppError, ValidationFailed
from app.utils import hmac_verify

logger = logging.getLogger(__name__)

router = APIRouter()


def _c(request: Request):
    return request.app.state.container


@router.post("/internal/mlflow/webhook")
async def mlflow_webhook(request: Request):
    c = _c(request)
    settings = c.settings
    trace_id = getattr(request.state, "trace_id", "")
    raw = await request.body()
    if len(raw) > settings.webhook_max_body_bytes:
        return error_response(413, "VALIDATION_FAILED", "webhook body too large", trace_id)
    signature = request.headers.get(settings.webhook_signature_header, "")
    if not signature or not hmac_verify(settings.webhook_hmac_secret, raw, signature):
        return error_response(401, "UNAUTHENTICATED", "bad webhook signature", trace_id)
    delivery_id = request.headers.get(settings.webhook_delivery_header)
    if not delivery_id:
        return error_response(400, "VALIDATION_FAILED", "missing delivery id", trace_id)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return error_response(400, "VALIDATION_FAILED", "invalid JSON body", trace_id)
    tenant_id = body.get("tenant_id")
    event_type = body.get("event")
    if not tenant_id or not event_type:
        return error_response(400, "VALIDATION_FAILED", "missing tenant_id/event", trace_id)

    await c.mirror_service.ingest_webhook(
        tenant_id=tenant_id, delivery_id=delivery_id, event_type=event_type, payload=body)
    # EXP-FR-011: application is async (handler stays fast). Fire-and-forget the
    # inbox applier so the mirror converges without blocking the 204.
    asyncio.create_task(_safe_apply(c, tenant_id))  # noqa: RUF006
    return Response(status_code=204)


async def _safe_apply(container, tenant_id: str) -> None:
    try:
        await container.mirror_service.apply_inbox_once(tenant_id)
    except Exception:  # noqa: BLE001
        logger.exception("inbox apply failed for tenant %s", tenant_id)


class McpInvokeRequest(BaseModel):
    tool_id: str
    version: str | None = None
    args: dict = {}
    tenant: str
    obo_sub: str | None = None
    agent_id: str | None = None


# tool_id -> the action the EFFECTIVE HUMAN must hold. Mirrors the REST route
# exposing the same capability (`POST /models/{id}/versions/{v}/promote` gates
# on experiment.model.update) — an agent's proposal-execution grant is not a
# permission bypass, just a durable record of a human's APPROVE.
_MCP_TOOL_ACTIONS = {
    "experiment.model.promote": "experiment.model.update",
}


def _mcp_output(status: int, output: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content={"output": output})


@router.post("/internal/v1/mcp/invoke")
async def mcp_invoke(request: Request, body: McpInvokeRequest,
                     spiffe: str = Depends(require_internal)):
    """The MCP backend facade tool-plane federates write-proposal tool execution
    to (TPL-FR-012) — the missing half of EXP-FR-052's write tools. Reached only
    after tool-plane's full enforcement pipeline (OPA + signed proposal grant
    bound to this tenant/tool/tier/args digest); this handler still re-checks
    authorization for the EFFECTIVE HUMAN (obo_sub) against the real OPA
    sidecar (defense-in-depth: the backend never blindly trusts the gateway).
    The promotion it creates is PENDING — experiment-service's own four-eyes
    (SelfApprovalForbidden) still governs the actual stage change."""
    c = _c(request)
    action = _MCP_TOOL_ACTIONS.get(body.tool_id)
    if action is None:
        return _mcp_output(404, {"error": f"unknown tool_id {body.tool_id!r}"})

    principal = Principal(
        sub=f"agent:{body.agent_id}" if body.agent_id else "svc:mcp-gateway",
        tenant_id=body.tenant, typ="agent_obo" if body.agent_id else "service",
        agent_id=body.agent_id, obo_sub=body.obo_sub,
        workspace_id=body.args.get("workspace_id"))

    # Defense-in-depth capability check for the EFFECTIVE HUMAN (obo_sub).
    # Evaluate it as a USER, not as agent_obo: the OPA authz_input agent_obo path
    # additionally requires the ACTION to be in the caller's delegated token
    # scopes (windrose_authz_input.rego `user_path`+`scope_ok`) — a gate tool-plane
    # already enforced upstream on the agent's signed toolset, and one this
    # internal facade never receives scopes to satisfy. What we must verify here
    # is simply whether the deciding human holds the action (via their rbac
    # projection: admin flag / workspace or tenant role actions). Checking the
    # obo_sub as agent_obo with empty scopes always deny-by-defaults regardless of
    # the human's real capability.
    authz_principal = principal
    if body.obo_sub:
        authz_principal = Principal(
            sub=body.obo_sub, tenant_id=body.tenant, typ="user",
            workspace_id=body.args.get("workspace_id"))
    if not await request.app.state.authz.allow(authz_principal, action, None):
        return _mcp_output(403, {"error": f"not allowed: {action}"})

    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    try:
        if body.tool_id == "experiment.model.promote":
            args = body.args
            out = await c.mcp.model_promote(
                ctx, model_id=args["model_id"], version=int(args["version"]),
                payload={"target_stage": args["target_stage"],
                         "rationale": args.get("rationale")})
        else:
            return _mcp_output(404, {"error": f"unknown tool_id {body.tool_id!r}"})
    except AppError as exc:
        return _mcp_output(exc.status, {"error": exc.message, "code": exc.code})
    except KeyError as exc:
        return _mcp_output(422, {"error": f"missing required arg {exc}"})
    except (TypeError, ValueError) as exc:
        return _mcp_output(422, {"error": str(exc)})
    return {"output": out}


@router.post("/internal/reconcile")
async def reconcile(request: Request):
    require_internal(request)
    c = _c(request)
    trace_id = getattr(request.state, "trace_id", "")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    tenant_id = body.get("tenant_id")
    if not tenant_id:
        raise ValidationFailed("tenant_id is required")
    result = await c.reconciliation_service.sweep_tenant(tenant_id)
    c.extras.setdefault("gauges", {})["mlflow_mirror_drift_total"] = result["drift_count"]
    c.extras["gauges"]["mlflow_mirror_lag_seconds"] = 0
    return {"data": result, "trace_id": trace_id}
