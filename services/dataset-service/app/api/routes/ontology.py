"""Domain ontology: a governed entity-TYPE registry (inc11). A capability pack
(or a tenant) declares the entity types its vertical operates on (Vendor,
Invoice, PaymentRun, ...) with their attributes + typed RELATIONSHIPS to other
types — the type-level domain model, distinct from dataset-derived semantic
entities (flat) and from entity RESOLUTION (which resolves instances)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from app.api.auth import Principal, require
from app.domain.errors import ValidationFailed

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


def _payload(e) -> dict:
    return {
        "id": e.id, "entity_key": e.entity_key, "workspace_id": e.workspace_id,
        "name": e.name, "description": e.description,
        "attributes": e.attributes, "relationships": e.relationships,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.post("/ontology/entities", status_code=201)
async def create_entity(
    request: Request, body: dict = Body(...),
    principal: Principal = Depends(require("dataset.ontology.create")),
):
    workspace_id = body.get("workspace_id")
    if not workspace_id:
        raise ValidationFailed("workspace_id is required")
    e = await _c(request).ontology_service.create(
        principal.ctx(), workspace_id, body)
    return {"data": _payload(e)}


@router.get("/ontology/entities")
async def list_entities(
    request: Request,
    principal: Principal = Depends(require("dataset.ontology.read")),
    workspace_id: str | None = Query(default=None, alias="filter[workspace_id]"),
):
    items = await _c(request).ontology_service.list(principal.ctx(), workspace_id)
    return {"data": [_payload(e) for e in items]}


@router.get("/ontology/entities/{entity_key}")
async def get_entity(
    request: Request, entity_key: str,
    principal: Principal = Depends(require("dataset.ontology.read")),
    workspace_id: str = Query(alias="filter[workspace_id]"),
):
    e = await _c(request).ontology_service.get(principal.ctx(), workspace_id, entity_key)
    return {"data": _payload(e)}


@router.delete("/ontology/entities/{entity_key}", status_code=204)
async def delete_entity(
    request: Request, entity_key: str,
    principal: Principal = Depends(require("dataset.ontology.delete")),
    workspace_id: str = Query(alias="filter[workspace_id]"),
):
    await _c(request).ontology_service.delete(principal.ctx(), workspace_id, entity_key)
    return Response(status_code=204)
