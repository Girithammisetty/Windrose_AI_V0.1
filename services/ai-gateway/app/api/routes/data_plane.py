"""OpenAI-compatible data plane: /v1/chat/completions, /v1/completions,
/v1/embeddings (AIG-FR-001/002/010)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, StreamingResponse

from app.config import REQUEST_CLASSES
from app.domain.entities import Attribution, PipelineResult
from app.domain.errors import ValidationFailed
from app.domain.pipeline import RequestCtx
from app.utils import uuid7

router = APIRouter(prefix="/v1")


def _bool_header(request: Request, name: str) -> bool:
    return request.headers.get(name, "").lower() in ("1", "true", "yes")


def _build_ctx(request: Request, default_class: str = "chat") -> RequestCtx:
    principal = request.state.principal
    key = request.state.virtual_key
    request_class = request.headers.get("x-windrose-request-class", default_class)
    if request_class not in REQUEST_CLASSES:
        raise ValidationFailed(
            f"unknown request class {request_class!r}",
            details=[{"field": "x-windrose-request-class",
                      "problem": f"allowed: {list(REQUEST_CLASSES)}"}],
        )
    # AIG-FR-002: x-windrose-tenant-id is ignored; tenant comes from the JWT.
    # Attribution headers are validated against JWT claims where overlapping.
    agent_id = request.headers.get("x-windrose-agent-id")
    agent_version = request.headers.get("x-windrose-agent-version")
    if principal.agent_id and agent_id and agent_id != principal.agent_id:
        raise ValidationFailed(
            "x-windrose-agent-id does not match the JWT agent_id claim"
        )
    if (principal.agent_version and agent_version
            and agent_version != principal.agent_version):
        raise ValidationFailed(
            "x-windrose-agent-version does not match the JWT claim"
        )
    min_rung_raw = request.headers.get("x-windrose-min-rung")
    try:
        min_rung = int(min_rung_raw) if min_rung_raw is not None else None
    except ValueError as exc:
        raise ValidationFailed("x-windrose-min-rung must be an integer") from exc
    return RequestCtx(
        request_id=str(uuid7()),
        tenant_id=principal.tenant_id,
        principal_sub=principal.sub,
        principal_typ=principal.typ,
        key=key,
        request_class=request_class,
        attribution=Attribution(
            workspace_id=request.headers.get("x-windrose-workspace-id"),
            user_id=principal.obo_sub or principal.sub,
            agent_id=agent_id or principal.agent_id,
            agent_version=agent_version or principal.agent_version,
            tool=request.headers.get("x-windrose-tool"),
            feature=request.headers.get("x-windrose-feature"),
        ),
        cell_cloud=principal.cell_cloud,
        trace_id=getattr(request.state, "trace_id", None),
        escalate=_bool_header(request, "x-windrose-escalate"),
        prior_request_id=request.headers.get("x-windrose-prior-request-id"),
        min_rung=min_rung,
        actor=principal.actor,
        via_agent=principal.via_agent,
    )


def _headers(ctx: RequestCtx, result: PipelineResult) -> dict:
    headers = {
        "x-windrose-request-id": ctx.request_id,
        "x-windrose-rung": str(result.rung),
        "x-windrose-cache": (
            "hit" if result.cache.startswith("hit") else result.cache
        ),
    }
    if result.deployment_id:
        headers["x-windrose-deployment"] = result.deployment_id  # AIG-FR-009b
    if result.degraded:
        headers["x-windrose-degraded"] = "budget"
    if result.guardrail_flags:
        headers["x-windrose-guardrail-flags"] = ",".join(result.guardrail_flags)
    return headers


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    ctx = _build_ctx(request)
    gateway = request.app.state.container.gateway
    result = await gateway.chat(ctx, body)
    if result.stream is not None:
        return StreamingResponse(result.stream, media_type="text/event-stream",
                                 headers=_headers(ctx, result))
    return JSONResponse(result.response, headers=_headers(ctx, result))


@router.post("/completions")
async def completions(request: Request):
    """Legacy completions: adapted onto the chat pipeline (AIG-FR-001)."""
    body = await request.json()
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise ValidationFailed("prompt must be a non-empty string")
    chat_body = {**body, "messages": [{"role": "user", "content": prompt}]}
    chat_body.pop("prompt", None)
    ctx = _build_ctx(request)
    gateway = request.app.state.container.gateway
    result = await gateway.chat(ctx, chat_body)
    if result.stream is not None:
        return StreamingResponse(result.stream, media_type="text/event-stream",
                                 headers=_headers(ctx, result))
    chat = result.response
    legacy = {
        "id": f"cmpl-{ctx.request_id}",
        "object": "text_completion",
        "created": chat["created"],
        "model": chat["model"],
        "choices": [{
            "index": 0,
            "text": chat["choices"][0]["message"]["content"],
            "finish_reason": chat["choices"][0]["finish_reason"],
        }],
        "usage": chat["usage"],
    }
    return JSONResponse(legacy, headers=_headers(ctx, result))


@router.post("/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    ctx = _build_ctx(request, default_class="embed")
    gateway = request.app.state.container.gateway
    result = await gateway.embeddings(ctx, body)
    return JSONResponse(result.response, headers=_headers(ctx, result))
