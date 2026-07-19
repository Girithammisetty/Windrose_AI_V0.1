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
    snapshot = installer.snapshot_bundle(manifest)  # durable version record for rollback
    async with db.tenant_tx(principal.tenant_id) as conn:
        await repo.create_install(
            conn, install_id=install_id, tenant_id=principal.tenant_id, workspace_id=ws,
            pack_name=manifest.name, pack_version=manifest.version,
            status="installing", plan=plan, created_by=principal.effective_user,
            operation="install", manifest_snapshot=snapshot)

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


@router.get("/installs/{install_id}/drift")
async def install_drift(
    request: Request, install_id: str,
    principal: Principal = Depends(require("pack.install.read")),
):
    """Detect DRIFT (PKG-FR-031): compare each object this install materialized to
    Core's current state and report it as in_sync / modified / missing / unverified.
    The pack's intended spec is re-derived from the install's stored bundle
    snapshot; a superseded install's objects are owned by its successor (all
    tombstoned) so drift is reported empty with a hint to check the head."""
    import tempfile  # noqa: PLC0415

    db, settings = request.app.state.db, request.app.state.settings
    async with db.tenant_tx(principal.tenant_id) as conn:
        row = await repo.get_install(conn, install_id)
        if row is None:
            raise NotFound(f"install {install_id} not found")
        ledger = [_ledger_view(m) for m in await repo.get_ledger(conn, install_id)]

    client = installer.build_client(settings, principal.tenant_id, str(row["workspace_id"]), get_bearer(request))
    snapshot = repo.jloads(row.get("manifest_snapshot")) or {}

    def _drift(manifest):
        return installer.detect_drift(client, ledger, manifest)

    if snapshot.get("files"):
        with tempfile.TemporaryDirectory(prefix="pack-drift-") as tmp:
            rows = await asyncio.to_thread(_drift, installer.rehydrate_bundle(snapshot, tmp))
    else:
        rows = await asyncio.to_thread(_drift, None)  # pre-snapshot install: presence-only

    summary = {
        "objects": len(rows),
        "in_sync": sum(1 for d in rows if d["status"] == "in_sync"),
        "modified": sum(1 for d in rows if d["status"] == "modified"),
        "missing": sum(1 for d in rows if d["status"] == "missing"),
        "unverified": sum(1 for d in rows if d["status"] == "unverified"),
        "content_checked": sum(1 for d in rows if d["contentChecked"]),
    }
    drifted = summary["modified"] + summary["missing"]
    return {"data": {"id": install_id, "pack": row["pack_name"], "version": row["pack_version"],
                     "workspaceId": str(row["workspace_id"]),
                     "superseded": bool(row.get("superseded_by")),
                     "drifted": drifted, "inSync": drifted == 0,
                     "summary": summary, "objects": rows}}


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


class TransitionRequest(BaseModel):
    dry_run: bool = False
    to_install_id: str | None = None  # rollback only: which prior install to restore


@router.post("/installs/{install_id}/upgrade")
async def upgrade_install(
    request: Request, install_id: str, body: TransitionRequest,
    principal: Principal = Depends(require("pack.install.execute")),
):
    """Upgrade a live install to the pack's CURRENT on-disk version (PKG-FR-003):
    diff the target version against this install's ledger, then materialize added
    components, re-apply retained ones (idempotent), and reverse removed ones. A
    new install row supersedes this one. `dry_run` returns just the diff."""
    db = request.app.state.db
    async with db.tenant_tx(principal.tenant_id) as conn:
        prior = await repo.get_install(conn, install_id)
        if prior is None:
            raise NotFound(f"install {install_id} not found")
        prior_ledger = [_ledger_view(m) for m in await repo.get_ledger(conn, install_id)]
    if prior.get("superseded_by"):
        raise ValidationFailed("this install has already been superseded; upgrade its successor")
    target = catalog.load_manifest(prior["pack_name"])  # current on-disk = the new version
    return await _transition(request, principal, prior, prior_ledger, target,
                             operation="upgrade", target_snapshot=installer.snapshot_bundle(target),
                             dry_run=body.dry_run)


@router.post("/installs/{install_id}/rollback")
async def rollback_install(
    request: Request, install_id: str, body: TransitionRequest,
    principal: Principal = Depends(require("pack.install.execute")),
):
    """Roll a live install back to a PRIOR version (PKG-FR-026): re-apply the
    target install's stored bundle snapshot verbatim (re-creating any component a
    later version removed) and reverse anything the current version added. The
    target defaults to the install this one superseded. A new install row
    supersedes the current one. `dry_run` returns just the diff."""
    import tempfile  # noqa: PLC0415

    db = request.app.state.db
    async with db.tenant_tx(principal.tenant_id) as conn:
        current = await repo.get_install(conn, install_id)
        if current is None:
            raise NotFound(f"install {install_id} not found")
        if current.get("superseded_by"):
            raise ValidationFailed("this install has already been superseded; roll back its successor")
        target_id = body.to_install_id or (str(current["supersedes"]) if current.get("supersedes") else None)
        if not target_id:
            raise ValidationFailed("no prior version to roll back to (this is the original install)")
        target_row = await repo.get_install(conn, target_id)
        if target_row is None:
            raise NotFound(f"rollback target install {target_id} not found")
        current_ledger = [_ledger_view(m) for m in await repo.get_ledger(conn, install_id)]

    snapshot = repo.jloads(target_row.get("manifest_snapshot")) or {}
    if not snapshot.get("files"):
        raise ValidationFailed(f"target install {target_id} has no stored bundle to restore")
    with tempfile.TemporaryDirectory(prefix="pack-rollback-") as tmp:
        target = installer.rehydrate_bundle(snapshot, tmp)
        return await _transition(request, principal, current, current_ledger, target,
                                 operation="rollback", target_snapshot=snapshot,
                                 dry_run=body.dry_run, target_version=target_row["pack_version"])


async def _transition(request: Request, principal, prior: dict, prior_ledger: list[dict],
                      target, *, operation: str, target_snapshot: dict, dry_run: bool,
                      target_version: str | None = None):
    """Shared upgrade/rollback core: diff, then (unless dry_run) materialize the
    target over the prior install, supersede the prior, and record a new row."""
    db, settings = request.app.state.db, request.app.state.settings
    ws = str(prior["workspace_id"])
    version = target_version or target.version
    diff = installer.diff_plan(prior_ledger, target)
    diff_view = {"added": diff["added"], "removed": diff["removed"], "retained": diff["retained"]}
    if dry_run:
        return {"data": {"install": str(prior["id"]), "pack": target.name, "operation": operation,
                         "fromVersion": prior["pack_version"], "toVersion": version,
                         "dry_run": True, "diff": diff_view}}

    client = installer.build_client(settings, principal.tenant_id, ws, get_bearer(request))
    origin_of = installer.origin_tag(target.name, version)
    new_ledger, pending_dashboards, removed_outcomes, _ = await asyncio.to_thread(
        installer.run_upgrade, client, target, prior_ledger, origin_of)

    failed = sum(1 for r in new_ledger if r["action"] == "failed")
    status = "failed" if failed else ("awaiting_approval" if pending_dashboards else "installed")
    reversed_n = sum(1 for o in removed_outcomes if o["deleted"])
    summary = {
        "operation": operation, "fromVersion": prior["pack_version"], "toVersion": version,
        "added": len(diff["added"]), "removed": len(diff["removed"]),
        "retained": len(diff["retained"]), "reversed": reversed_n,
        "created": sum(1 for r in new_ledger if r["action"] == "create"),
        "failed": failed,
    }

    new_id = str(uuid.uuid4())
    async with db.tenant_tx(principal.tenant_id) as conn:
        await repo.create_install(
            conn, install_id=new_id, tenant_id=principal.tenant_id, workspace_id=ws,
            pack_name=target.name, pack_version=version, status=status,
            plan=[{"kind": o["kind"], "name": o["name"], "action": "add"} for o in diff["added"]]
                 + [{"kind": o["kind"], "name": o["name"], "action": "remove"} for o in diff["removed"]]
                 + [{"kind": o["kind"], "name": o["name"], "action": "retain"} for o in diff["retained"]],
            created_by=principal.effective_user, operation=operation,
            supersedes=str(prior["id"]), manifest_snapshot=target_snapshot)
        await repo.add_materialized(conn, new_id, principal.tenant_id, new_ledger)
        await repo.set_install_status(conn, new_id, status, summary)
        # Reverse-then-retire the prior: removed objects are already deleted in
        # Core; tombstone ALL prior rows so ownership of retained objects moves to
        # the new install and a later uninstall of the prior never double-reverses.
        await repo.tombstone_all_ledger(conn, prior["id"])
        await repo.supersede_install(conn, str(prior["id"]), new_id)

    return {"data": {"id": new_id, "pack": target.name, "version": version, "workspaceId": ws,
                     "operation": operation, "supersedes": str(prior["id"]), "status": status,
                     "summary": summary, "diff": diff_view,
                     "removedOutcomes": removed_outcomes, "ledger": new_ledger}}


def _install_view(r: dict) -> dict:
    return {
        "id": str(r["id"]), "pack": r["pack_name"], "version": r["pack_version"],
        "workspaceId": str(r["workspace_id"]), "status": r["status"],
        "operation": r.get("operation", "install"),
        "supersedes": str(r["supersedes"]) if r.get("supersedes") else None,
        "supersededBy": str(r["superseded_by"]) if r.get("superseded_by") else None,
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
