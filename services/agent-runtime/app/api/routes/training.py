"""SLM distillation training control plane (milestone 3/4) — submit / track /
promote. Consumes the versioned SFT datasets from milestone 2; the GPU LoRA
compute runs behind the GpuTrainer port (fails honestly when no GPU is wired).
Tenant-scoped (RLS)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request

from app.api.auth import principal_of
from app.domain.errors import NotFound, ValidationFailed
from app.domain.training import TrainingJobService

router = APIRouter(prefix="/api/v1")


def _job_view(j) -> dict:
    return {
        "job_id": j.job_id, "archetype": j.archetype, "sft_dataset_id": j.sft_dataset_id,
        "base_model": j.base_model, "status": j.status, "params": j.params,
        "mlflow_run_ref": j.mlflow_run_ref, "adapter_id": j.adapter_id, "error": j.error,
        "created_by": j.created_by,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


def _adapter_view(a) -> dict:
    return {
        "adapter_id": a.adapter_id, "training_job_id": a.training_job_id,
        "archetype": a.archetype, "base_model": a.base_model, "adapter_uri": a.adapter_uri,
        "checksum": a.checksum, "model_alias": a.model_alias,
        "promotion_status": a.promotion_status, "eval_result_ref": a.eval_result_ref,
        "target_rung_alias": a.target_rung_alias,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _svc(request: Request) -> TrainingJobService:
    c = request.app.state.container
    return TrainingJobService(c.store, c.trainer)


@router.post("/training-jobs", status_code=201)
async def submit_training_job(request: Request, body: dict = Body(...)):
    principal = await principal_of(request)
    agent_key = body.get("agent_key") or body.get("archetype")
    sft_dataset_id = body.get("sft_dataset_id")
    if not agent_key or not sft_dataset_id:
        raise ValidationFailed("agent_key and sft_dataset_id are required")
    job = await _svc(request).submit(
        tenant_id=principal.tenant_id, agent_key=agent_key, sft_dataset_id=sft_dataset_id,
        base_model=body.get("base_model"), params=body.get("params") or {},
        created_by=principal.sub)
    return {"data": _job_view(job)}


@router.get("/training-jobs")
async def list_training_jobs(
    request: Request,
    archetype: str | None = Query(default=None, alias="filter[archetype]"),
    limit: int = Query(default=50, ge=1, le=200),
):
    principal = await principal_of(request)
    c = request.app.state.container
    rows = await c.store.list_training_jobs(principal.tenant_id, archetype=archetype, limit=limit)
    return {"data": [_job_view(j) for j in rows], "page": {"next_cursor": None, "has_more": False}}


@router.get("/training-jobs/{job_id}")
async def get_training_job(request: Request, job_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    j = await c.store.get_training_job(principal.tenant_id, job_id)
    if j is None:
        raise NotFound("training job not found")
    return {"data": _job_view(j)}


@router.get("/slm-adapters")
async def list_adapters(
    request: Request,
    archetype: str | None = Query(default=None, alias="filter[archetype]"),
    limit: int = Query(default=50, ge=1, le=200),
):
    principal = await principal_of(request)
    c = request.app.state.container
    rows = await c.store.list_slm_adapters(principal.tenant_id, archetype=archetype, limit=limit)
    return {"data": [_adapter_view(a) for a in rows], "page": {"next_cursor": None, "has_more": False}}  # noqa: E501


@router.get("/slm-adapters/{adapter_id}")
async def get_adapter(request: Request, adapter_id: str):
    principal = await principal_of(request)
    c = request.app.state.container
    a = await c.store.get_slm_adapter(principal.tenant_id, adapter_id)
    if a is None:
        raise NotFound("adapter not found")
    return {"data": _adapter_view(a)}


@router.post("/slm-adapters/{adapter_id}/promote")
async def promote_adapter(request: Request, adapter_id: str, body: dict = Body(default={})):
    principal = await principal_of(request)
    a = await _svc(request).promote(
        tenant_id=principal.tenant_id, adapter_id=adapter_id,
        eval_result_ref=body.get("eval_result_ref"))
    return {"data": _adapter_view(a)}


@router.post("/slm-adapters/{adapter_id}/demote")
async def demote_adapter(request: Request, adapter_id: str):
    principal = await principal_of(request)
    a = await _svc(request).demote(tenant_id=principal.tenant_id, adapter_id=adapter_id)
    return {"data": _adapter_view(a)}
