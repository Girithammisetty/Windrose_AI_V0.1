"""Memory write / retrieve / browse endpoints (MEM-FR-010/011/020/050)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.auth import Principal, get_principal
from app.api.schemas import (
    BatchWriteIn,
    EditMemoryIn,
    RetrieveIn,
    UnquarantineIn,
    WriteMemoryIn,
    data_envelope,
)
from app.domain.entities import (
    SCOPE_SESSION,
    SCOPE_USER,
    SCOPE_WORKSPACE,
)
from app.domain.errors import ScopeDenied, ValidationFailed
from app.domain.services import WriteRequest
from app.utils import clamp_limit_or_default

router = APIRouter(prefix="/api/v1")

# Canonical action names (<service>.<resource>.<verb>, closed verb set per
# RBC-FR-022) — registered with rbac at startup (app/registration.py) and
# present in rbac's static catalog. Retrieval is a read; new memories are
# creates; edits + unquarantine are updates.
CREATE = "memory.memory.create"
READ = "memory.memory.read"
UPDATE = "memory.memory.update"
DELETE = "memory.memory.delete"


async def _authz(request: Request, principal: Principal, action: str, urn=None) -> None:
    if not await request.app.state.authz.allow(principal, action, urn):
        from app.domain.errors import PermissionDenied
        raise PermissionDenied(f"missing permission {action}")


def _writable_scopes(principal: Principal) -> set[str]:
    """Scopes the caller may write (MEM-FR-010 step 1). Agents carry the
    runtime-forwarded ``mem_scopes_writable`` claim (agent version memory_policy);
    users may write only their own user scope."""
    if principal.typ in ("agent_obo", "agent_autonomous"):
        claim = getattr(principal, "_scopes_writable", None)
        return set(claim) if claim else {SCOPE_SESSION, SCOPE_USER, SCOPE_WORKSPACE, "tenant"}
    return {SCOPE_USER, SCOPE_SESSION}


def _check_write_scope(principal: Principal, scope: str, scope_ref: str) -> None:
    if scope not in _writable_scopes(principal):
        raise ScopeDenied(f"caller may not write scope {scope}")
    if scope == SCOPE_USER and scope_ref != principal.effective_user:
        raise ScopeDenied("users may only write their own user scope")


@router.post("/memories")
async def write_memory(request: Request, body: WriteMemoryIn):
    principal = get_principal(request)
    await _authz(request, principal, CREATE)
    _check_write_scope(principal, body.scope, body.scope_ref)
    c = request.app.state.container
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    res = await c.write_service.write(ctx, WriteRequest(
        scope=body.scope, scope_ref=body.scope_ref, content=body.content,
        provenance=body.provenance.model_dump(), confidence=body.confidence,
        tags=body.tags))
    return data_envelope({"memory_id": res.memory_id, "status": res.status,
                          "merged": res.merged, "session": res.session})


@router.post("/memories/batch")
async def write_batch(request: Request, body: BatchWriteIn):
    principal = get_principal(request)
    await _authz(request, principal, CREATE)
    reqs = []
    for item in body.items:
        _check_write_scope(principal, item.scope, item.scope_ref)
        reqs.append(WriteRequest(
            scope=item.scope, scope_ref=item.scope_ref, content=item.content,
            provenance=item.provenance.model_dump(), confidence=item.confidence,
            tags=item.tags))
    c = request.app.state.container
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    results = await c.write_service.write_batch(ctx, reqs)
    return data_envelope(results)


def _resolve_scopes(principal: Principal, body: RetrieveIn, membership_ok: dict) -> list:
    """Server-verified scope_refs (MEM-FR-020): user scope_ref = OBO user;
    workspace scope_ref must be a workspace the OBO user belongs to."""
    resolved = []
    for scope in body.scopes:
        if scope == SCOPE_SESSION:
            continue  # session retrieval is a separate path (Redis)
        ref = body.scope_refs.get(scope)
        if scope == SCOPE_USER:
            ref = principal.effective_user
        elif scope == SCOPE_WORKSPACE:
            if not ref:
                raise ValidationFailed("workspace scope requires scope_refs.workspace")
            if not membership_ok.get(ref, False):
                raise ScopeDenied(f"user not a member of workspace {ref}")
        elif scope == "tenant":
            ref = principal.tenant_id
        if not ref:
            raise ValidationFailed(f"scope {scope} requires a scope_ref")
        resolved.append((scope, ref))
    return resolved


@router.post("/retrieve")
async def retrieve(request: Request, body: RetrieveIn):
    principal = get_principal(request)
    await _authz(request, principal, READ)
    c = request.app.state.container
    # Pre-check workspace membership (BR-10) for any requested workspace refs.
    membership_ok = {}
    if SCOPE_WORKSPACE in body.scopes:
        ws = body.scope_refs.get(SCOPE_WORKSPACE)
        if ws:
            membership_ok[ws] = await c.deps.membership.is_member(
                principal.tenant_id, principal.effective_user, ws)
    scopes = _resolve_scopes(principal, body, membership_ok)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    results, degraded = await c.retrieval_service.retrieve(
        ctx, query_text=body.query_text, query_embedding=body.query_embedding,
        scopes=scopes, corpora=body.corpora, top_k=body.top_k,
        min_confidence=body.min_confidence, tags=body.tags,
        snapshot_ver=body.snapshot_ver, include_debug=body.include_debug)
    data = []
    for r in results:
        item = {"kind": r.kind, "content": r.content, "score": round(r.score, 6),
                "content_disposition": "untrusted"}  # BR-12
        if r.kind == "memory":
            item.update({"scope": r.scope, "memory_id": r.memory_id,
                         "provenance": r.provenance})
        else:
            item.update({"corpus": r.corpus, "chunk_id": r.chunk_id,
                         "source_urn": r.source_urn, "snapshot_ver": r.snapshot_ver})
        if r.debug is not None:
            item["debug"] = r.debug
        data.append(item)
    return data_envelope(data, degraded=degraded)


@router.get("/memories")
async def browse(request: Request,
                 scope: str | None = None, limit: int = Query(default=50),
                 cursor: str | None = None,
                 status: str | None = Query(default=None, alias="filter[status]"),
                 tags: str | None = Query(default=None, alias="filter[tags]"),
                 scope_ref: str | None = None):
    principal = get_principal(request)
    await _authz(request, principal, READ)
    c = request.app.state.container
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    # Users see only their own user-scope records.
    if principal.typ == "user" and "*" not in principal.scopes:
        scope = scope or SCOPE_USER
        if scope == SCOPE_USER:
            scope_ref = principal.effective_user
    tag_list = [t for t in (tags.split(",") if tags else []) if t]
    page = await c.admin_service.list_memories(
        ctx, scope=scope, status=status, tags=tag_list or None,
        scope_ref=scope_ref, limit=clamp_limit_or_default(limit), cursor=cursor)
    return data_envelope([_memory_view(r) for r in page.items],
                         page={"next_cursor": page.next_cursor, "has_more": page.has_more})


@router.get("/memories/{memory_id}")
async def get_memory(request: Request, memory_id: str):
    principal = get_principal(request)
    await _authz(request, principal, READ)
    c = request.app.state.container
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    rec = await c.admin_service.get(ctx, memory_id)
    return data_envelope(_memory_view(rec, full=True))


@router.patch("/memories/{memory_id}")
async def edit_memory(request: Request, memory_id: str, body: EditMemoryIn):
    principal = get_principal(request)
    await _authz(request, principal, UPDATE)
    c = request.app.state.container
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    rec = await c.admin_service.edit(ctx, memory_id, body.content)
    return data_envelope(_memory_view(rec, full=True))


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(request: Request, memory_id: str):
    principal = get_principal(request)
    await _authz(request, principal, DELETE)
    c = request.app.state.container
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    await c.admin_service.delete(ctx, memory_id)


@router.post("/memories/{memory_id}/unquarantine")
async def unquarantine(request: Request, memory_id: str, body: UnquarantineIn):
    principal = get_principal(request)
    await _authz(request, principal, UPDATE)
    if not body.reason:
        raise ValidationFailed("reason is required")
    c = request.app.state.container
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    rec = await c.admin_service.unquarantine(ctx, memory_id, body.reason)
    return data_envelope(_memory_view(rec, full=True))


def _memory_view(rec, full: bool = False) -> dict:
    view = {
        "memory_id": rec.memory_id, "scope": rec.scope, "scope_ref": rec.scope_ref,
        "content": rec.content, "confidence": rec.confidence, "status": rec.status,
        "tags": rec.tags, "provenance": rec.provenance,
        "retrieval_count": rec.retrieval_count,
        "classifier_score": rec.classifier_score,
        "ttl_expires_at": rec.ttl_expires_at.isoformat() if rec.ttl_expires_at else None,
    }
    if full:
        view["merged_from"] = rec.merged_from
        view["revalidate_at"] = rec.revalidate_at.isoformat() if rec.revalidate_at else None
    return view
