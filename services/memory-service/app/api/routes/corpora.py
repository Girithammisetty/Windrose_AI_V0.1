"""RAG corpus admin (MEM-FR-030/031/033), docs push, status."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.auth import get_principal
from app.api.schemas import CorpusIn, CorpusPatchIn, DocumentIn, RebuildIn, data_envelope

router = APIRouter(prefix="/api/v1")

CORPUS_ADMIN = "memory.corpus.admin"


async def _authz(request: Request, principal, action) -> None:
    if not await request.app.state.authz.allow(principal, action, None):
        from app.domain.errors import PermissionDenied
        raise PermissionDenied(f"missing permission {action}")


def _corpus_view(c) -> dict:
    return {"corpus_key": c.corpus_key, "source": c.source, "chunking": c.chunking,
            "active_embedding_ver": c.active_embedding_ver, "refresh": c.refresh,
            "anonymization_profile": c.anonymization_profile, "status": c.status}


@router.post("/corpora", status_code=201)
async def register_corpus(request: Request, body: CorpusIn):
    principal = get_principal(request)
    await _authz(request, principal, CORPUS_ADMIN)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    corpus = await request.app.state.container.corpus_service.register(
        ctx, body.model_dump(exclude_none=True))
    return data_envelope(_corpus_view(corpus))


@router.patch("/corpora/{corpus_key}")
async def patch_corpus(request: Request, corpus_key: str, body: CorpusPatchIn):
    principal = get_principal(request)
    await _authz(request, principal, CORPUS_ADMIN)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    corpus = await request.app.state.container.corpus_service.patch(
        ctx, corpus_key, body.model_dump(exclude_none=True))
    return data_envelope(_corpus_view(corpus))


@router.post("/corpora/{corpus_key}/rebuild", status_code=202)
async def rebuild_corpus(request: Request, corpus_key: str, body: RebuildIn):
    principal = get_principal(request)
    await _authz(request, principal, CORPUS_ADMIN)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    report = await request.app.state.container.corpus_service.rebuild(
        ctx, corpus_key, body.embedding_model_ver)
    return data_envelope(report)


@router.get("/corpora/{corpus_key}/status")
async def corpus_status(request: Request, corpus_key: str):
    principal = get_principal(request)
    await _authz(request, principal, CORPUS_ADMIN)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    return data_envelope(
        await request.app.state.container.corpus_service.status(ctx, corpus_key))


@router.post("/corpora/docs/documents", status_code=201)
async def push_document(request: Request, body: DocumentIn):
    principal = get_principal(request)
    await _authz(request, principal, CORPUS_ADMIN)
    ctx = principal.ctx(getattr(request.state, "trace_id", None))
    n = await request.app.state.container.corpus_service.add_document(
        ctx, body.source_urn, body.content)
    return data_envelope({"source_urn": body.source_urn, "chunks": n})
