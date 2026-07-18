"""Entity-resolution API (BRD 56).

inc1: run first-party resolution over a dataset's rows and read the resolved-
entity view + below-auto merge candidates. inc2: persist the run (versioned
config + clusters + lineage + merge queue), read prior runs/candidates, and
apply a four-eyes-approved merge. Read-only over the SOURCE — a LINK layer,
never mutating records (ER-FR-050)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.auth import Principal, require

router = APIRouter(prefix="/api/v1")


class ScoringFieldIn(BaseModel):
    column: str
    weight: float = 1.0


class ResolutionConfigIn(BaseModel):
    entity_type: str = "entity"
    deterministic_keys: list[list[str]] = Field(default_factory=list)
    scoring_fields: list[ScoringFieldIn] = Field(default_factory=list)
    blocking_fields: list[str] = Field(default_factory=list)
    auto_merge_threshold: float = 0.85
    review_threshold: float = 0.60


class ResolveRequest(BaseModel):
    pk_column: str
    config: ResolutionConfigIn
    row_limit: int = 20000
    # inc2: persist the run (versioned config + clusters + lineage + candidates)
    # so decisions can read the resolved entities and stewards can review merges.
    persist: bool = True


@router.post("/datasets/{dataset_id}/entity-resolution")
async def resolve_entities(
    request: Request,
    dataset_id: str,
    body: ResolveRequest,
    principal: Principal = Depends(require("dataset.entity.execute")),
):
    c = request.app.state.container
    result = await c.dataset_service.resolve_entities(
        principal.tenant_id, dataset_id,
        config=body.config.model_dump(), pk_column=body.pk_column,
        row_limit=body.row_limit, persist=body.persist,
        ctx=principal.ctx(), created_by=principal.effective_user)
    return {"data": result}


@router.get("/datasets/{dataset_id}/resolution-runs")
async def list_resolution_runs(
    request: Request,
    dataset_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require("dataset.entity.read")),
):
    c = request.app.state.container
    runs = await c.dataset_service.list_resolution_runs(principal.tenant_id, dataset_id, limit)
    return {"data": runs}


@router.get("/resolution-runs/{run_id}")
async def get_resolution_run(
    request: Request,
    run_id: str,
    principal: Principal = Depends(require("dataset.entity.read")),
):
    c = request.app.state.container
    run = await c.dataset_service.get_resolution_run(principal.tenant_id, run_id)
    return {"data": run}


@router.get("/resolution-runs/{run_id}/merge-candidates")
async def list_merge_candidates(
    request: Request,
    run_id: str,
    status: str | None = Query(default=None),
    principal: Principal = Depends(require("dataset.entity.read")),
):
    c = request.app.state.container
    cands = await c.dataset_service.list_merge_candidates(principal.tenant_id, run_id, status)
    return {"data": cands}
