from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.auth import Principal, get_bearer, require
from app.domain import catalog, installer
from app.domain.errors import NotFound, ValidationFailed
from app.store import repo

router = APIRouter(prefix="/api/v1")


class InstallRequest(BaseModel):
    pack: str
    version: str | None = None
    workspace_id: str | None = None
    dry_run: bool = False


def _ws(req: InstallRequest, principal: Principal) -> str:
    ws = req.workspace_id or principal.workspace_id
    if not ws:
        raise ValidationFailed("workspace_id is required (none on the request or the token)")
    return ws


@router.post("/installs")
async def create_install(
    request: Request, body: InstallRequest,
    principal: Principal = Depends(require("pack.install.execute")),
):
    """Plan (dry_run) or execute an install of a pack into a workspace.

    Execution materializes the pack AS the caller (their JWT is forwarded to
    Core), records the plan + the origin-tagged materialization ledger, and
    returns both."""
    manifest = catalog.load_manifest(body.pack)  # raises if missing/invalid
    if manifest is None:
        raise NotFound(f"pack {body.pack!r} not found")
    ws = _ws(body, principal)
    settings = request.app.state.settings
    db = request.app.state.db
    user_jwt = get_bearer(request)
    client = installer.build_client(settings, principal.tenant_id, ws, user_jwt)

    plan = await asyncio.to_thread(installer.plan, client, manifest)
    if body.dry_run:
        return {"data": {"pack": manifest.name, "version": manifest.version,
                         "workspace_id": ws, "dry_run": True, "plan": plan}}

    install_id = str(uuid.uuid4())
    async with db.tenant_tx(principal.tenant_id) as conn:
        await repo.create_install(
            conn, install_id=install_id, tenant_id=principal.tenant_id, workspace_id=ws,
            pack_name=manifest.name, pack_version=manifest.version,
            status="installing", plan=plan, created_by=principal.effective_user)

    origin_of = installer.origin_tag(manifest.name, manifest.version)
    ledger, pending_dashboards = await asyncio.to_thread(installer.run_install, client, manifest, origin_of)

    # If dashboards are pending, try to complete now — succeeds only when the
    # pack's semantic model is already published (e.g. an idempotent re-install);
    # on a fresh install it stays awaiting_approval until a steward publishes it.
    if pending_dashboards and not any(r["action"] == "failed" for r in ledger):
        dash, ok, _detail = await asyncio.to_thread(installer.run_complete, client, manifest, origin_of)
        if ok:
            ledger += dash
            pending_dashboards = False

    failed = sum(1 for r in ledger if r["action"] == "failed")
    submitted = sum(1 for r in ledger if r["action"] == "submitted")
    status = "failed" if failed else ("awaiting_approval" if pending_dashboards else "installed")
    summary = {
        "created": sum(1 for r in ledger if r["action"] == "create"),
        "submitted": submitted, "failed": failed, "actions": len(ledger),
        "deferred": len([o for o in plan if o["action"] == "deferred"]),
        "awaiting_dashboards": len([o for o in plan if o["action"] == "after_approval"]) if pending_dashboards else 0,
    }
    async with db.tenant_tx(principal.tenant_id) as conn:
        await repo.add_materialized(conn, install_id, principal.tenant_id, ledger)
        await repo.set_install_status(conn, install_id, status, summary)

    return {"data": {"id": install_id, "pack": manifest.name, "version": manifest.version,
                     "workspace_id": ws, "status": status, "summary": summary, "ledger": ledger}}


@router.post("/installs/{install_id}/complete")
async def complete_install(
    request: Request, install_id: str,
    principal: Principal = Depends(require("pack.install.execute")),
):
    """Phase 2: after a steward has approved the pack's semantic model, materialize
    the dashboards (their measure projection now resolves) and flip the install to
    installed. Returns the dashboard ledger additions, or 422 if still awaiting
    approval."""
    db = request.app.state.db
    settings = request.app.state.settings
    user_jwt = get_bearer(request)
    async with db.tenant_tx(principal.tenant_id) as conn:
        row = await repo.get_install(conn, install_id)
        if row is None:
            raise NotFound(f"install {install_id} not found")
    manifest = catalog.load_manifest(row["pack_name"])
    client = installer.build_client(settings, principal.tenant_id, str(row["workspace_id"]), user_jwt)
    origin_of = installer.origin_tag(manifest.name, manifest.version)

    dash, ok, detail = await asyncio.to_thread(installer.run_complete, client, manifest, origin_of)
    if not ok:
        raise ValidationFailed(detail)

    async with db.tenant_tx(principal.tenant_id) as conn:
        await repo.add_materialized(conn, install_id, principal.tenant_id, dash)
        await repo.set_install_status(conn, install_id, "installed",
                                      {**(repo.jloads(row.get("summary")) or {}),
                                       "dashboards": sum(1 for r in dash if r["action"] == "create"),
                                       "awaiting_dashboards": 0})
    return {"data": {"id": install_id, "status": "installed",
                     "dashboards": [_ledger_view({**d, "tombstoned": False}) for d in dash]}}


@router.get("/installs")
async def list_installs(
    request: Request, workspace_id: str | None = None,
    principal: Principal = Depends(require("pack.install.read")),
):
    db = request.app.state.db
    async with db.tenant_tx(principal.tenant_id) as conn:
        rows = await repo.list_installs(conn, workspace_id)
    return {"data": [_install_view(r) for r in rows]}


@router.get("/installs/{install_id}")
async def get_install(
    request: Request, install_id: str,
    principal: Principal = Depends(require("pack.install.read")),
):
    db = request.app.state.db
    async with db.tenant_tx(principal.tenant_id) as conn:
        row = await repo.get_install(conn, install_id)
        if row is None:
            raise NotFound(f"install {install_id} not found")
        ledger = await repo.get_ledger(conn, install_id)
    view = _install_view(row)
    view["ledger"] = [_ledger_view(m) for m in ledger]
    return {"data": view}


@router.post("/installs/{install_id}/uninstall")
async def uninstall(
    request: Request, install_id: str,
    principal: Principal = Depends(require("pack.install.execute")),
):
    """Reverse a pack install (PKG-FR-025): delete objects whose Core service
    exposes a revert verb; tombstone the rest (retained, pack-origin cleared)."""
    db = request.app.state.db
    settings = request.app.state.settings
    user_jwt = get_bearer(request)
    async with db.tenant_tx(principal.tenant_id) as conn:
        row = await repo.get_install(conn, install_id)
        if row is None:
            raise NotFound(f"install {install_id} not found")
        ledger = [_ledger_view(m) for m in await repo.get_ledger(conn, install_id)]

    client = installer.build_client(settings, principal.tenant_id, str(row["workspace_id"]), user_jwt)
    outcomes = await asyncio.to_thread(installer.run_uninstall, client, ledger)

    deleted = sum(1 for o in outcomes if o["deleted"])
    async with db.tenant_tx(principal.tenant_id) as conn:
        for o in outcomes:
            await repo.mark_tombstoned(conn, o["ledger_id"], o["detail"])
        await repo.set_install_status(
            conn, install_id, "uninstalled",
            {"reversed": deleted, "tombstoned": len(outcomes) - deleted})

    return {"data": {"id": install_id, "status": "uninstalled",
                     "reversed": deleted, "tombstoned": len(outcomes) - deleted,
                     "outcomes": outcomes}}


def _install_view(r: dict) -> dict:
    return {
        "id": str(r["id"]), "pack": r["pack_name"], "version": r["pack_version"],
        "workspaceId": str(r["workspace_id"]), "status": r["status"],
        "plan": repo.jloads(r.get("plan")) or [], "summary": repo.jloads(r.get("summary")) or {},
        "createdBy": r.get("created_by"),
        "createdAt": r["created_at"].isoformat() if r.get("created_at") else None,
    }


def _ledger_view(m: dict) -> dict:
    return {
        "id": str(m["id"]), "kind": m["kind"], "identity": m["identity"],
        "target_urn": m.get("target_urn"), "target_id": m.get("target_id"),
        "origin": m["origin"], "action": m["action"], "detail": m.get("detail"),
        "reversible": m.get("reversible", False), "tombstoned": m.get("tombstoned", False),
    }
