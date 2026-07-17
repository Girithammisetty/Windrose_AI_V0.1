"""Component registry catalog (PIPE-FR-050)."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Request

from app.api.auth import Principal, require
from app.api.schemas import component_payload

router = APIRouter(prefix="/api/v1")


def _c(request: Request):
    return request.app.state.container


@router.get("/components")
async def list_components(request: Request,
                          principal: Principal = Depends(
                              require("pipeline.component.read"))):
    c = _c(request)
    grouped: dict[str, list] = defaultdict(list)
    for comp in c.catalog_service.list_components():
        payload = component_payload(comp)
        grouped[payload["component_type"]].append(payload)
    return {"data": {"catalog_version": c.settings.component_catalog_version,
                     "groups": grouped}}


@router.get("/components/{name}")
async def get_component(request: Request, name: str,
                        principal: Principal = Depends(
                            require("pipeline.component.read"))):
    c = _c(request)
    return {"data": component_payload(c.catalog_service.get_component(name))}
