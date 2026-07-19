from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import Principal, require
from app.domain import catalog
from app.domain.errors import NotFound

router = APIRouter(prefix="/api/v1")


@router.get("/packs")
async def list_packs(_: Principal = Depends(require("pack.pack.read"))):
    """The pack catalog (read live from the packs/ tree). PKG-FR-020 discovery."""
    return {"data": catalog.list_packs()}


@router.get("/packs/{name}")
async def get_pack(name: str, _: Principal = Depends(require("pack.pack.read"))):
    pack = catalog.get_pack(name)
    if pack is None:
        raise NotFound(f"pack {name!r} not found in the catalog")
    return {"data": pack}


@router.get("/packs/{name}/lint")
async def lint_pack(name: str, _: Principal = Depends(require("pack.pack.read"))):
    """Deep pack-authoring lint (packctl.lint) for one catalog pack — content +
    cross-reference findings beyond the manifest schema. `data.ok` is False iff
    any error finding. Pure/offline; the same checks `packctl lint` runs in CI."""
    report = catalog.lint_pack(name)
    if report is None:
        raise NotFound(f"pack {name!r} not found in the catalog")
    return {"data": report}
