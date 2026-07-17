"""SLM SFT datasets — curation + read API (distillation milestone 2).

`POST /sft-datasets` curates the transcript corpus for one archetype (agent_key)
into a new immutable, versioned SFT dataset; the read endpoints browse the
datasets and export the frozen chat-format rows (JSONL). Tenant-scoped (RLS)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.auth import principal_of
from app.domain.errors import NotFound, ValidationFailed
from app.domain.sft_curation import SftCurator

router = APIRouter(prefix="/api/v1")


def _ds_view(d) -> dict:
    return {
        "dataset_id": d.dataset_id, "agent_key": d.agent_key, "version": d.version,
        "status": d.status, "row_count": d.row_count, "source_count": d.source_count,
        "curation_params": d.curation_params, "checksum": d.checksum,
        "consent_verified": d.consent_verified, "created_by": d.created_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.post("/sft-datasets", status_code=201)
async def curate_sft_dataset(request: Request, body: dict = Body(...)):
    principal = await principal_of(request)
    c = request.app.state.container
    agent_key = body.get("agent_key")
    if not agent_key:
        raise ValidationFailed("agent_key is required")
    params = body.get("params") or {}
    curator = SftCurator(c.store)
    ds = await curator.curate(
        tenant_id=principal.tenant_id, agent_key=agent_key,
        created_by=principal.sub, params=params)
    return {"data": _ds_view(ds)}


@router.get("/sft-datasets")
async def list_sft_datasets(
    request: Request,
    agent_key: str | None = Query(default=None, alias="filter[agent_key]"),
    limit: int = Query(default=50, ge=1, le=200),
):
    principal = await principal_of(request)
    c = request.app.state.container
    rows = await c.store.list_sft_datasets(principal.tenant_id, agent_key=agent_key, limit=limit)
    return {"data": [_ds_view(d) for d in rows],
            "page": {"next_cursor": None, "has_more": False}}


@router.get("/sft-datasets/{dataset_id}")
async def get_sft_dataset(request: Request, dataset_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    d = await c.store.get_sft_dataset(principal.tenant_id, dataset_id)
    if d is None:
        raise NotFound("sft dataset not found")
    return {"data": _ds_view(d)}


@router.get("/sft-datasets/{dataset_id}/examples")
async def export_sft_examples(
    request: Request, dataset_id: str,
    limit: int = Query(default=1000, ge=1, le=10000),
):
    """Export the frozen training rows as JSONL (one chat example per line) —
    the exact artifact the LoRA trainer (milestone 3) consumes."""
    principal = await principal_of(request)
    c = request.app.state.container
    d = await c.store.get_sft_dataset(principal.tenant_id, dataset_id)
    if d is None:
        raise NotFound("sft dataset not found")
    rows = await c.store.list_sft_examples(principal.tenant_id, dataset_id, limit=limit)
    import json

    body = "\n".join(json.dumps({"messages": r.messages}, ensure_ascii=False) for r in rows)
    return PlainTextResponse(body + ("\n" if body else ""), media_type="application/x-ndjson")
