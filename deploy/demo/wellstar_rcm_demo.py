#!/usr/bin/env python3
"""Wellstar prospect demo — build + rehearse the denial-recovery journey.

Builds a REAL demo tenant on the running local stack (no mocks, no seeded
authz facts — everything flows the product paths):

  1. provision tenant `wellstar-demo` (identity real engine) + rbac seed
  2. demo users with REAL role memberships (dev-login personas)
  3. upload the synthetic RCM CSVs (wellstar_rcm_data.py) AS the tenant —
     tenant data via the product ingestion API, per the no-dummy-data rule
  4. install healthcare-provider-rcm v2 via pack-service (datasets bind by
     same-name reuse), four-eyes-publish both semantic models, complete
     dashboards (charts warm against the synthetic data)
  5. create the open denial worklist (cases from the denial rows), assign
     the hero to the director (implicit editor grant -> execution rights)

`--rehearse` then runs the full governed arc on a spare hero case:
  decision table (dry-run trace, then real) -> governed proposal ->
  four-eyes approval by the director -> disposition APPLIED to the case.

Usage:
  deploy/e2e/.venv/bin/python deploy/demo/wellstar_rcm_demo.py          # build
  deploy/e2e/.venv/bin/python deploy/demo/wellstar_rcm_demo.py --rehearse
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "packs"))
sys.path.insert(0, os.path.join(REPO, "deploy", "e2e"))
sys.path.insert(0, os.path.join(REPO, "deploy", "e2e", "lib"))

import common as c  # noqa: E402
import install_packs_multitenant as ipm  # noqa: E402
from packctl.client import Endpoints, PlatformClient  # noqa: E402

# DEMO_TENANT_SLUG picks the tenant name AND the persona emails/subs (all
# derived from it) — set it to run a second, independent demo tenant (e.g. a
# from-scratch live-provisioning walkthrough) without touching the rehearsed
# "wellstar-demo" tenant. Defaults preserve the original single-tenant demo.
DEMO_SLUG = os.environ.get("DEMO_TENANT_SLUG", "wellstar-demo")
TENANT_NAME = DEMO_SLUG
DISPLAY = os.environ.get("DEMO_TENANT_DISPLAY", "Wellstar Demo — Provider Revenue Cycle")
PACK = "healthcare-provider-rcm"
PACK_SVC = os.environ.get("PACK_URL", "http://localhost:8309")

ADMIN_SUB = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_SLUG}-admin"))
DIRECTOR_SUB = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_SLUG}-director"))
SPECIALIST_SUB = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{DEMO_SLUG}-specialist"))

# System-role memberships (real grants; the projector materializes both
# projections from these): admin runs the demo, director APPROVES + applies
# (Case Manager holds tool.tool.execute + case.disposition.approve),
# specialist works the queue.
USERS = {
    f"admin@{DEMO_SLUG}": (ADMIN_SUB, ["Admin", "Use case Admin"]),
    f"director@{DEMO_SLUG}": (DIRECTOR_SUB, ["Case Manager", "Use case Admin"]),
    f"specialist@{DEMO_SLUG}": (SPECIALIST_SUB, ["Case Analyst"]),
}

say, ok, warn, die = ipm.say, ipm.ok, ipm.warn, ipm.die


def http(path: str, tok: str, body=None, method="GET", base=PACK_SVC):
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"err": e.code, "body": e.read().decode()[:300]}


def build_client(tid: str, ws: str) -> PlatformClient:
    session = str(uuid.uuid4())
    return PlatformClient(
        endpoints=Endpoints(
            ingestion=c.INGESTION, dataset=c.DATASET, semantic="http://localhost:8086",
            query="http://localhost:8085", chart="http://localhost:8318", case=c.CASE,
            rbac=c.RBAC, agent=c.AGENT_RUNTIME, memory=c.MEMORY, pipeline=c.PIPELINE,
            identity=c.IDENTITY, eval="http://localhost:8312", experiment=c.EXPERIMENT),
        tenant_id=tid, workspace_id=ws,
        author_token=lambda: c.user_token(ADMIN_SUB, tid, ["*"], workspace_id=ws),
        approver_token=lambda: c.user_token(DIRECTOR_SUB, tid, ["*"], workspace_id=ws),
        agent_token=lambda: c.agent_obo_token(ADMIN_SUB, tid, ["*"], session, workspace_id=ws),
        log=lambda *a: None)


def onboard() -> tuple[str, str]:
    tid = ipm.ensure_tenant(TENANT_NAME, DISPLAY, f"admin@{DEMO_SLUG}")
    ws = ipm.ensure_rbac_seeded(tid)
    ipm.bootstrap_admins(tid, [ADMIN_SUB, DIRECTOR_SUB])

    # Real memberships for every demo user (same durable rows rbac AddMember
    # writes), then the REAL projection rebuild.
    import psycopg
    dsn = os.environ.get("RBAC_DATABASE_URL",
                         "postgres://datacern:datacern_dev@localhost:5432/rbac")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tid,))
        for _email, (sub, groups) in USERS.items():
            for g in groups:
                row = conn.execute(
                    "SELECT id FROM groups WHERE tenant_id = %s AND group_type = 'permission' "
                    "AND lower(name) = lower(%s)", (tid, g)).fetchone()
                if not row:
                    warn(f"group {g!r} missing")
                    continue
                conn.execute(
                    "INSERT INTO members (id, tenant_id, group_id, user_id) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (group_id, user_id) DO NOTHING",
                    (str(uuid.uuid4()), tid, row[0], sub))
    ipm.rebuild_projection(tid)
    provision_identity_users(tid)
    tok = c.user_token(ADMIN_SUB, tid, ["*"], workspace_id=ws)
    caps, _, admin = ipm.poll_caps(tok)
    if not (admin or caps):
        die("admin capabilities never materialized")
    ok(f"tenant ready: {tid} ws={ws}")
    return tid, ws


FULL_NAMES = {
    f"admin@{DEMO_SLUG}": "Demo Admin",
    f"director@{DEMO_SLUG}": "Revenue Cycle Director",
    f"specialist@{DEMO_SLUG}": "Denials Specialist",
}


def provision_identity_users(tid: str) -> None:
    """ACTIVE identity users whose id == the sub dev-login sessions carry.

    Production linking is invite -> accept(idp_subject) -> active, and the
    session subject IS the identity user id (token_oidc.go). Dev-login mints
    subs from personas.json, so the identity rows must carry those same ids or
    the assignable-user directory, assignment grants, and approvals key on
    different identifiers. Same durable-row bootstrap pattern (and rationale)
    as bootstrap_admins above. Idempotent; re-keys the provisioning-created
    owner row (invited, generated id) to the persona sub."""
    import psycopg
    dsn = os.environ.get("IDENTITY_DATABASE_URL",
                         "postgres://datacern:datacern_dev@localhost:5432/identity")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tid,))
        for email, (sub, _groups) in USERS.items():
            row = conn.execute(
                "SELECT id, status FROM users WHERE tenant_id = %s "
                "AND lower(email) = lower(%s)", (tid, email)).fetchone()
            if row and str(row[0]) != sub:
                # provisioning created this row with a generated id; re-key it
                conn.execute("DELETE FROM invitations WHERE tenant_id = %s "
                             "AND user_id = %s", (tid, row[0]))
                conn.execute("DELETE FROM users WHERE tenant_id = %s AND id = %s",
                             (tid, row[0]))
                row = None
            if row is None:
                conn.execute(
                    "INSERT INTO users (id, tenant_id, email, full_name, status, "
                    "idp_subject, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,'active',%s,now(),now()) "
                    "ON CONFLICT (id) DO NOTHING",
                    (sub, tid, email, FULL_NAMES[email], sub))
            else:
                conn.execute(
                    "UPDATE users SET status = 'active', full_name = %s, "
                    "idp_subject = %s, updated_at = now() "
                    "WHERE tenant_id = %s AND id = %s",
                    (FULL_NAMES[email], sub, tid, sub))
    ok("identity users active (id == session sub): "
       + ", ".join(f"{e} ({FULL_NAMES[e]})" for e in USERS))


def upload_data(client: PlatformClient) -> None:
    datadir = os.path.join(HERE, "wellstar-rcm")
    for fname, dsname in (("rcm_claims.csv", "rcm-claims"), ("rcm_remits.csv", "rcm-remits"),
                          ("rcm_denials.csv", "rcm-denials"), ("rcm_ar_aging.csv", "rcm-ar-aging")):
        with open(os.path.join(datadir, fname), "rb") as f:
            urn = client.ensure_dataset(dsname.replace("-", "_"), dsname, f.read())
        if not urn:
            die(f"upload {dsname} failed")
        ok(f"dataset {dsname} -> {urn.rsplit('/', 1)[-1][:8]}…")


def install_pack(tid: str, ws: str, client: PlatformClient) -> str:
    admin = client.author_token()
    res = http("/api/v1/installs", admin, {"pack": PACK, "workspace_id": ws}, "POST")
    if "err" in res:
        die(f"install: {res}")
    d = res["data"]
    failed = [r for r in d["ledger"] if r["action"] == "failed"]
    ok(f"pack installed: {d['summary']} ")
    for r in failed:
        warn(f"failed: {r['kind']}/{r['identity']}: {r['detail'][:120]}")
    if failed:
        die("install had failures")

    # Four-eyes: director publishes BOTH semantic models, then dashboards.
    appr = client.approver_token()
    models = http(f"/api/v1/models?filter[workspace_id]={ws}", admin,
                  base="http://localhost:8086")["data"]
    for m in models:
        if not m.get("published_version_id"):
            r = http(f"/api/v1/models/{m['id']}/versions/1/approve", appr, {}, "POST",
                     base="http://localhost:8086")
            ok(f"semantic model published (four-eyes): {m['name']}"
               if "err" not in r else f"approve {m['name']}: {r}")
    comp = http(f"/api/v1/installs/{d['id']}/complete", admin, {}, "POST")
    if "err" in comp:
        die(f"complete: {comp}")
    for dash in comp["data"]["dashboards"]:
        ok(f"dashboard: {dash['identity']} — {dash.get('detail', '')}")
    return d["id"]


def create_worklist(tid: str, ws: str, client: PlatformClient) -> dict[str, str]:
    """Open denial cases from the synthetic denial rows (projections carry the
    decision-table columns), hero + spares assigned to the DIRECTOR so the
    implicit editor grant authorizes the eventual disposition apply."""
    denials = {r["denial_id"]: r for r in
               csv.DictReader(open(os.path.join(HERE, "wellstar-rcm", "rcm_denials.csv")))}
    ds = client.find_dataset("rcm-denials")
    urn = client.dataset_urn(ds)
    open_ids = ["DN-3001", "DN-3002", "DN-3003", "DN-3004", "DN-3005", "DN-3006",
                "DN-3901", "DN-3902", "DN-3903"]
    rows = []
    for did in open_ids:
        r = denials[did]
        rows.append({"row_pk": did, "severity": "high" if float(r["denied_amount"]) >= 5000 else "medium",
                     "display_projection": {
                         "denial_id": did, "claim_id": r["claim_id"],
                         "payer_name": r["payer_name"], "service_line": r["service_line"],
                         "carc_code": r["carc_code"], "denial_category": r["denial_category"],
                         "denial_reason_text": r["denial_reason_text"],
                         "denied_amount": r["denied_amount"],
                         "appeal_status": r["appeal_status"],
                         "appeal_deadline_days": r["appeal_deadline_days"]}})
    ids = client.create_cases("wellstar_denial_worklist", urn, rows, due_days=5)
    ok(f"{len(ids)} denial cases in the worklist")
    by_pk = dict(zip(open_ids, ids))

    admin = client.author_token()
    for did in ("DN-3001", "DN-3901", "DN-3902", "DN-3903"):
        cid = by_pk.get(did)
        if cid:
            a = http(f"/api/v1/cases/{cid}/assign", admin,
                     {"assignee_id": DIRECTOR_SUB}, "POST", base=c.CASE)
            ok(f"{did} assigned to director" if "err" not in a else f"assign {did}: {a}")
    return by_pk


def enable_tools(tid: str, ws: str) -> None:
    """Per-tenant tool enablement (a real governance gate: tools are opt-in
    per tenant). Without this the approved disposition apply is denied with
    TOOL_DISABLED. Must run under a token whose tenant == this tenant.

    Registers the tool in tool-plane's GLOBAL catalog first (idempotent —
    CreateTool/publish both no-op on conflict, see driver.py's docstring):
    on a from-scratch stack (fresh tool_plane db) the catalog is empty and the
    per-tenant enable PUT 404s until something registers case.apply_disposition
    once. Normally the e2e suite does this; the demo must be able to stand
    alone on a freshly wiped platform, so do it here too."""
    import driver  # noqa: PLC0415 (deploy/e2e on sys.path — see header)
    driver.register_apply_tool()

    tok = c.user_token(ADMIN_SUB, tid, ["*"], workspace_id=ws)
    for tool in ("case.apply_disposition",):
        r = http(f"/api/v1/tenants/self/tools/{tool}", tok, {"enabled": True}, "PUT",
                 base=c.TOOL_REGISTRY)
        ok(f"tool enabled for tenant: {tool}" if "err" not in r else f"enable {tool}: {r}")


def personas(tid: str, ws: str) -> None:
    entries = {}
    for email, (sub, groups) in USERS.items():
        entries[email] = {"sub": sub, "tenantId": tid, "workspaceId": ws,
                          "scopes": ["*"], "roles": groups}
    ipm.merge_personas(entries)
    ok(f"dev logins merged: {', '.join(USERS)} (restart ui-web if it was already running)")


def rehearse(tid: str, ws: str, client: PlatformClient) -> None:
    """Full governed arc on the freshest un-resolved spare hero case."""
    admin = client.author_token()
    director = client.approver_token()

    dms = http("/api/v1/decision-models", admin, base=c.AGENT_RUNTIME)["data"]
    table = next(d for d in dms if d.get("workspace_id") == ws and "triage" in d["name"].lower())
    if table.get("status") != "published":
        r = http(f"/api/v1/decision-models/{table['id']}/approve", director, {}, "POST",
                 base=c.AGENT_RUNTIME)
        ok("decision table published (four-eyes)" if "err" not in r else f"table approve: {r}")

    # freshest spare: an assigned, unresolved DN-39xx case
    cases = http(f"/api/v1/cases?workspace_id={ws}&limit=100", admin, base=c.CASE)["data"]
    spare = next((x for x in cases
                  if (x.get("display_projection") or {}).get("denial_id", "").startswith("DN-39")
                  and not x.get("disposition_id")), None)
    if spare is None:
        die("no unresolved spare hero case left — re-run the builder to recreate DN-39xx cases")
    cid = spare["id"]
    dp = spare["display_projection"]
    say(f"rehearsing on {dp['denial_id']} (${dp['denied_amount']}, "
        f"{dp['appeal_deadline_days']} days left)")

    s = http(f"/api/v1/cases/{cid}/start", director, {}, "POST", base=c.CASE)
    ok("director started the case" if "err" not in s else f"start: {s}")

    dry = http(f"/api/v1/decision-models/{table['id']}/evaluate?dry_run=true", admin,
               {"case_id": cid}, "POST", base=c.AGENT_RUNTIME)["data"]
    ok(f"DRY-RUN: matched={dry['matched']} outcome={dry['outcome']} — {dry['explanation'][:90]}")

    ev = http(f"/api/v1/decision-models/{table['id']}/evaluate", admin,
              {"case_id": cid}, "POST", base=c.AGENT_RUNTIME)["data"]
    ok(f"governed proposal created: {ev['proposal_id']}")

    dec = http(f"/api/v1/proposals/{ev['proposal_id']}/decide", director,
               {"action": "approve"}, "POST", base=c.AGENT_RUNTIME)
    ok(f"director approved (four-eyes): {dec.get('data', dec).get('status', dec)}")

    time.sleep(3)
    case = http(f"/api/v1/cases/{cid}", admin, base=c.CASE)["data"]
    if case.get("disposition_id"):
        ok(f"DISPOSITION APPLIED — case resolved, note: {(case.get('resolution_note') or '')[:80]}")
    else:
        die("disposition did not land on the case — check tool-plane invocation_log deny_reason")


def main() -> int:
    tid, ws = onboard()
    client = build_client(tid, ws)
    if "--rehearse" in sys.argv:
        rehearse(tid, ws, client)
        return 0
    upload_data(client)
    install_pack(tid, ws, client)
    enable_tools(tid, ws)
    create_worklist(tid, ws, client)
    personas(tid, ws)
    print(f"\nDemo tenant ready. tenant={tid} workspace={ws}")
    print("Logins (dev login, any password): " + ", ".join(USERS))
    print("Rehearse the governed arc:  wellstar_rcm_demo.py --rehearse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
