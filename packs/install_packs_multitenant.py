#!/usr/bin/env python3
"""Install every vertical pack (BRD 24-31) into its OWN tenant, each with
per-role test users, so the packs can be tested manually in the UI as a real
multi-tenant SaaS: one tenant per vertical, different users per tenant, every
user landing in a nav shaped by their pack role.

What it does per pack (all idempotent — safe to re-run):
  1. provision the tenant through identity-service's REAL provisioning API
     (stable name, reused on re-run — no tenant drift);
  2. ensure rbac's tenant bootstrap (system permission groups + the real
     "Default use case" workspace) and align every id to it;
  3. bootstrap two operator subjects (author + distinct four-eyes approver)
     as REAL Admin group members (direct membership rows — the same
     chicken-and-egg bootstrap seed_platform.py documents: rbac's member API
     is action-gated and a fresh tenant has no admin member yet), then
     trigger rbac's REAL projection rebuild and verify capabilities live;
  4. install the investigation-framework LIBRARY pack first where BRD 31
     declares the vertical consumes it (payer-fwa-siu, banking-aml);
  5. install the vertical pack through packctl (the same Core public APIs
     the product UI uses — datasets ingested, semantic models four-eyes
     published, dashboards warm-verified, cases, roles, agents, memories);
  6. create one test user PER PACK ROLE through rbac's REAL member API
     (PUT /groups/{id}/members/{user}) using the tenant admin's token,
     rebuild the projection, and verify each user's /me/capabilities is
     non-empty and differentiated;
  7. merge every login into deploy/local/run/personas.json (the map ui-web's
     dev login reads) and restart ui-web so the logins are live.

Run:  cd Windrose-ai/packs && ../deploy/e2e/.venv/bin/python install_packs_multitenant.py
      (add --no-restart-ui to leave the running UI untouched;
       add --only wr-aml to (re)do a single tenant)

Manual-testing cheat sheet is printed at the end AND written to
packs/MULTITENANT_LOGINS.md. Dev login accepts the email with any password.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
E2E = os.path.join(REPO, "deploy", "e2e")
LOCAL = os.path.join(REPO, "deploy", "local")
sys.path.insert(0, HERE)
sys.path.insert(0, E2E)
sys.path.insert(0, os.path.join(E2E, "lib"))

import psycopg  # noqa: E402
import requests  # noqa: E402

import common as c  # noqa: E402  (harness IdP — real RS256 JWTs all services verify)
from packctl.client import Endpoints, PlatformClient  # noqa: E402
from packctl.installer import install  # noqa: E402
from packctl.manifest import load_manifest  # noqa: E402

B, G, Y, R, N = "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def say(m):
    print(f"{B}==>{N} {m}", flush=True)


def ok(m):
    print(f"  {G}ok{N} {m}", flush=True)


def warn(m):
    print(f"  {Y}!!{N} {m}", flush=True)


def die(m):
    print(f"  {R}FAIL{N} {m}", flush=True)
    sys.exit(1)


# ---- the tenant plan --------------------------------------------------------
# (pack_dir, tenant_name, display_name, short)  — short is the login email
# domain label: admin@<short>.windrose etc.
PACKS = [
    ("insurance-claims-payer", "wr-payer", "Windrose Payer Claims Co", "payer"),
    ("care-management-medicare", "wr-caremgmt", "Windrose Care Management", "caremgmt"),
    ("healthcare-provider-rcm", "wr-rcm", "Windrose Provider RCM", "rcm"),
    ("payer-fwa-siu", "wr-fwa", "Windrose Payer FWA-SIU", "fwa"),
    ("pharmacy-benefit-mgmt", "wr-pbm", "Windrose Pharmacy Benefits", "pbm"),
    ("post-acute-care", "wr-pac", "Windrose Post-Acute Care", "pac"),
    ("banking-aml", "wr-aml", "Windrose Banking AML", "aml"),
    ("card-disputes", "wr-disputes", "Windrose Card Disputes", "disputes"),
    ("pharmacovigilance", "wr-pv", "Windrose Pharmacovigilance", "pv"),
    ("workers-comp-claims", "wr-wcomp", "Windrose Workers Comp", "wcomp"),
    ("trade-compliance", "wr-trade", "Windrose Trade Compliance", "trade"),
    ("trucking-claims", "wr-trucking", "Windrose Trucking Claims", "trucking"),
    ("warranty-claims", "wr-warranty", "Windrose Warranty Claims", "warranty"),
    ("mortgage-loss-mitigation", "wr-lossmit", "Windrose Loss Mitigation", "lossmit"),
    ("credit-disputes", "wr-fcra", "Windrose Credit Disputes", "fcra"),
    ("background-screening", "wr-screening", "Windrose Background Screening", "screening"),
    ("trust-safety-appeals", "wr-appeals", "Windrose Trust & Safety", "appeals"),
    ("device-complaints", "wr-mdr", "Windrose Device Vigilance", "mdr"),
    ("underwriting-intake", "wr-uw", "Windrose Underwriting Intake", "uw"),
    ("chargeback-representment", "wr-merchant", "Windrose Merchant Disputes", "merchant"),
    ("seller-vetting", "wr-marketplace", "Windrose Marketplace Integrity", "marketplace"),
    ("benefits-appeals", "wr-benefits", "Windrose Benefits Adjudication", "benefits"),
    ("utility-inspections", "wr-utility", "Windrose Utility Inspections", "utility"),
    ("construction-claims", "wr-construction", "Windrose Construction Claims", "construction"),
    ("ap-invoice-audit", "wr-apaudit", "Windrose AP Audit", "apaudit"),
    ("manufacturing-mrb", "wr-mrb", "Windrose Manufacturing Quality", "mrb"),
    ("tax-notices", "wr-tax", "Windrose Tax Notices", "tax"),
]
# BRD 31: investigation-framework is a LIBRARY pack consumed via depends_on —
# it has no surface of its own, so it is installed INTO its consumers' tenants
# (base layer first), exactly the layering the BRD requires.
LIBRARY_PACK = "investigation-framework"
LIBRARY_CONSUMERS = {"payer-fwa-siu", "banking-aml"}

# Tenant-admin login scopes: seed_platform.PERSONA_SCOPES + tenant.admin (the
# one raw JWT scope agent-runtime's kill-switch routes still check directly).
ADMIN_SCOPES = [
    "case.case.read", "case.case.write", "case.disposition.read", "case.disposition.write",
    "dataset.dataset.read", "dataset.profile.read",
    "experiment.experiment.read", "experiment.model.read",
    "chart.dashboard.read", "usage.report.read",
    "agent.proposal.read", "agent.proposal.decide", "agent.run.create",
    "tenant.admin",
]

RBAC_DSN = os.environ.get("RBAC_DATABASE_URL", "postgres://{u}:{pw}@{h}:{p}/rbac".format(
    u=os.environ.get("PGUSER", "windrose"), pw=os.environ.get("PGPASSWORD", "windrose_dev"),
    h=os.environ.get("PGHOST", "localhost"), p=os.environ.get("PGPORT", "5432")))

PERSONAS_JSON = os.path.join(LOCAL, "run", "personas.json")
REPORT_MD = os.path.join(HERE, "MULTITENANT_LOGINS.md")

# Tenant statuses still usable for install (mirrors deploy/e2e/lib/seed.py).
USABLE = {"active", "provisioning", "pending", "degraded"}


def J():
    return {"Content-Type": "application/json"}


def req(method, url, tok, **kw):
    headers = kw.pop("headers", {})
    headers["Authorization"] = f"Bearer {tok}"
    return requests.request(method, url, headers=headers, timeout=60, **kw)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ---- 1. tenant provisioning (identity-service, stable-name idempotent) ------
def ensure_tenant(name: str, display: str, owner_email: str) -> str:
    su = c.superadmin_token()
    r = req("GET", f"{c.IDENTITY}/api/v1/tenants?limit=100", su)
    if r.status_code == 200:
        for t in (r.json().get("tenants") or r.json().get("data") or []):
            if (t.get("name") or "").lower() == name.lower() and \
                    (t.get("status") or "active") in USABLE:
                ok(f"tenant {name!r} reused: {t['id']} (status={t.get('status')})")
                return t["id"]
    body = {"name": name, "display_name": display, "owner_email": owner_email,
            "tier": "pool", "cloud": "aws", "publish": True}
    r = req("POST", f"{c.IDENTITY}/api/v1/tenants", su, headers={
        **J(), "Idempotency-Key": f"packs-{name}"}, json=body)
    if r.status_code not in (200, 201, 202):
        die(f"tenant create {name!r}: {r.status_code} {r.text[:300]}")
    tid = r.json()["tenant"]["id"]
    ok(f"tenant {name!r} provisioned: {tid}")
    for _ in range(20):  # poll provisioning steps to done (best-effort)
        s = req("GET", f"{c.IDENTITY}/api/v1/tenants/{tid}/provisioning", su)
        steps = s.json().get("steps", []) if s.status_code == 200 else []
        states = {x["step_name"]: x["status"] for x in steps}
        if states and all(v == "succeeded" for v in states.values()):
            ok(f"provisioning complete: {sorted(states)}")
            break
        if any(v == "failed" for v in states.values()):
            warn(f"provisioning step failed (continuing): {states}")
            break
        time.sleep(1)
    return tid


# ---- 2. rbac tenant bootstrap + real default workspace ----------------------
def ensure_rbac_seeded(tid: str) -> str:
    su = c.superadmin_token()
    r = req("POST", f"{c.RBAC}/api/v1/admin/tenants/{tid}/seed", su, headers=J(), json={})
    if r.status_code == 200:
        ok(f"rbac system groups + default workspace ensured: {r.json()}")
    else:
        warn(f"rbac tenant seed: {r.status_code} {r.text[:160]}")
    with psycopg.connect(RBAC_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM workspaces WHERE tenant_id = %s "
                    "AND lower(name) = lower(%s)", (tid, "Default use case"))
        row = cur.fetchone()
    if not row:
        die(f"rbac default workspace missing for tenant {tid}")
    ws = str(row[0])
    ok(f"workspace (rbac 'Default use case'): {ws}")
    return ws


# ---- 3. operator bootstrap (author + four-eyes approver as REAL Admins) -----
def bootstrap_admins(tid: str, subs: list[str]) -> None:
    """First-admin chicken-and-egg bootstrap, same pattern (and same Postgres
    rows rbac's AddMember writes) as deploy/local/seed_platform.py: rbac's own
    member API is action-gated and a fresh tenant has no admin member yet.
    Everything AFTER this (pack roles, per-role test users) flows through
    rbac's real HTTP APIs under these admins' tokens."""
    added = 0
    with psycopg.connect(RBAC_DSN, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tid,))
        row = conn.execute(
            "SELECT id FROM groups WHERE tenant_id = %s AND group_type = 'permission' "
            "AND lower(name) = 'admin'", (tid,)).fetchone()
        if not row:
            die(f"Admin permission group missing for tenant {tid}")
        for sub in subs:
            cur = conn.execute(
                "INSERT INTO members (id, tenant_id, group_id, user_id) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (group_id, user_id) DO NOTHING",
                (str(uuid.uuid4()), tid, row[0], sub))
            added += cur.rowcount
    ok(f"{added} Admin membership(s) written (bootstrap; durable Postgres rows)")
    rebuild_projection(tid)


def rebuild_projection(tid: str) -> None:
    su = c.superadmin_token()
    r = req("POST", f"{c.RBAC}/api/v1/admin/projection/rebuild?tenant={tid}",
            su, headers=J(), json={})
    if r.status_code not in (200, 202):
        warn(f"projection rebuild: {r.status_code} {r.text[:160]}")


def poll_caps(tok: str, tries: int = 40, delay: float = 0.5):
    for _ in range(tries):
        r = req("GET", f"{c.RBAC}/api/v1/me/capabilities", tok)
        if r.status_code == 200:
            b = r.json()
            caps = b.get("capabilities") or []
            if caps:
                return caps, b.get("roles") or [], bool(b.get("admin"))
        time.sleep(delay)
    return [], [], False


# ---- 4/5. pack install through packctl's real-API client --------------------
def tenant_client(tid: str, ws: str, author_sub: str, approver_sub: str) -> PlatformClient:
    session = str(uuid.uuid4())
    return PlatformClient(
        endpoints=Endpoints(), tenant_id=tid, workspace_id=ws,
        author_token=lambda: c.user_token(author_sub, tid, ["*"], workspace_id=ws),
        approver_token=lambda: c.user_token(approver_sub, tid, ["*"], workspace_id=ws),
        agent_token=lambda: c.agent_obo_token(author_sub, tid, ["*"], session,
                                              workspace_id=ws),
    )


def install_pack(pack_dir: str, client: PlatformClient) -> dict:
    manifest = load_manifest(os.path.join(HERE, pack_dir))
    result = install(manifest, client, ledger_dir=os.path.join(HERE, pack_dir, ".ledgers"))
    status = "installed" if result.ok else "FAILED"
    ok(f"{pack_dir}: {status} — ledger {os.path.relpath(result.ledger_path, HERE)}")
    if not result.ok:
        for a in client.actions:
            if a.get("action") == "failed":
                warn(f"  {a['kind']}/{a['identity']}: {a.get('detail', '')[:200]}")
        die(f"pack {pack_dir} failed to install — see ledger")
    return {"pack": manifest.name, "version": manifest.version,
            "ledger": str(result.ledger_path)}


def pack_roles(pack_dir: str) -> list[dict]:
    """Every role the pack declares: [{name, actions}] (read from the same
    component files packctl installs, so personas always match the pack)."""
    import yaml
    manifest = load_manifest(os.path.join(HERE, pack_dir))
    roles = []
    for comp in manifest.components_of("roles"):
        with open(os.path.join(HERE, pack_dir, comp.file)) as f:
            for role in (yaml.safe_load(f) or []):
                roles.append({"name": role["name"], "actions": role.get("actions", [])})
    return roles


# ---- 6. per-role test users through rbac's REAL member API ------------------
def ensure_role_users(tid: str, ws: str, admin_tok: str, roles: list[dict],
                      short: str) -> list[dict]:
    g = req("GET", f"{c.RBAC}/api/v1/groups?filter[group_type]=permission&limit=200",
            admin_tok)
    groups = {grp["name"]: grp["id"] for grp in (g.json().get("data") or [])} \
        if g.status_code == 200 else {}
    users = []
    for role in roles:
        rslug = slug(role["name"])
        sub, email = f"user-{rslug}-{short}", f"{rslug}@{short}.windrose"
        gid = groups.get(role["name"])
        if not gid:
            warn(f"no permission group for role {role['name']!r} — skipping user")
            continue
        r = req("PUT", f"{c.RBAC}/api/v1/groups/{gid}/members/{sub}", admin_tok,
                headers=J(), json={})
        if r.status_code not in (200, 201, 204, 409):
            warn(f"add member {email}: {r.status_code} {r.text[:160]}")
            continue
        users.append({"email": email, "sub": sub, "role": role["name"],
                      "scopes": role["actions"]})
    rebuild_projection(tid)
    for u in users:  # live verify: non-empty caps under a NARROW-scoped token
        tok = c.user_token(u["sub"], tid, u["scopes"], workspace_id=ws)
        caps, roles_seen, admin = poll_caps(tok)
        if caps:
            ok(f"{u['email']:44s} caps={len(caps):3d} roles={roles_seen}")
        else:
            warn(f"{u['email']:44s} capabilities EMPTY — login will show bare nav")
    return users


# ---- 7. ui-web dev-login map + restart --------------------------------------
def merge_personas(entries: dict) -> None:
    current = {}
    if os.path.exists(PERSONAS_JSON):
        with open(PERSONAS_JSON) as f:
            try:
                current = json.load(f)
            except Exception:
                warn("existing personas.json unreadable — rebuilding it")
    current.update(entries)
    os.makedirs(os.path.dirname(PERSONAS_JSON), exist_ok=True)
    with open(PERSONAS_JSON, "w") as f:
        f.write(json.dumps(current, indent=0))
    ok(f"persona login map merged: {PERSONAS_JSON} ({len(current)} logins)")


def restart_ui() -> None:
    """Relaunch ui-web with the merged personas map (dev login reads
    WINDROSE_PERSONAS from process env at boot) — mirrors up.sh start_ui."""
    say("restarting ui-web so the new tenant logins are live")
    script = f'''
set -e
source "{E2E}/config.env"
# ui-web needs node@20's toolchain (its corepack pnpm supports node 20; the
# bare /opt/homebrew/bin/pnpm 11 requires node 22, and an nvm node16 may also
# shadow) — must come AFTER config.env, which prepends /opt/homebrew/bin.
export PATH="/opt/homebrew/opt/node@20/bin:$PATH"
pkill -f "next dev -p $PORT_UI" 2>/dev/null || true
sleep 1
personas="$(cat "{PERSONAS_JSON}")"
privjwk="$("$PY" "{E2E}/lib/common.py" jwk_private)"
pubjwk="$("$PY" "{E2E}/lib/common.py" jwk_public)"
cd "{REPO}/services/ui-web"
env AUTH_MODE=dev \
  JWT_ISSUER="$WR_ISS" JWT_AUDIENCE="$WR_AUD" \
  DEV_JWT_PRIVATE_JWK="$privjwk" DEV_JWT_PUBLIC_JWK="$pubjwk" \
  WINDROSE_PERSONAS="$personas" \
  BFF_URL="$BFF_URL/graphql" \
  REALTIME_HUB_URL="$REALTIME_URL" NEXT_PUBLIC_REALTIME_HUB_URL="$REALTIME_URL" \
  AGENT_RUNTIME_URL="$AGENT_RUNTIME_URL" \
  nohup pnpm exec next dev -p "$PORT_UI" > "$LOG_DIR/ui.log" 2>&1 &
for i in $(seq 1 90); do
  curl -sf -o /dev/null "$UI_URL/login" && exit 0
  sleep 1
done
echo "ui-web did not serve /login in 90s" >&2; exit 1
'''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    if r.returncode == 0:
        ok("ui-web restarted and serving /login with the merged persona map")
    else:
        warn(f"ui-web restart failed: {r.stderr.strip()[:300]}\n"
             "     restart it manually (see deploy/local/up.sh start_ui) — "
             "logins take effect once ui-web re-reads personas.json")


# ---- report (shared state: onboard/cleanup scripts update the same sheet) ----
STATE_JSON = os.path.join(HERE, ".multitenant_state.json")


def state_load() -> dict:
    if os.path.exists(STATE_JSON):
        try:
            with open(STATE_JSON) as f:
                return json.load(f)
        except Exception:
            warn("state file unreadable — starting fresh")
    return {}


def state_save(state: dict) -> None:
    with open(STATE_JSON, "w") as f:
        f.write(json.dumps(state, indent=2))


def write_report(rows: list[dict]) -> None:
    """Merge rows into the shared tenant state and regenerate the cheat sheet
    from ALL known tenants (so single-tenant onboarding and cleanup keep the
    sheet complete rather than clobbering it)."""
    state = state_load()
    for row in rows:
        state[row["tenant_name"]] = row
    state_save(state)
    render_report(state)


def render_report(state: dict) -> None:
    lines = ["# Multi-tenant pack logins (manual testing)", "",
             "Dev login at http://localhost:3000/login — enter the email, any password.",
             "Each tenant is a fully isolated (RLS) vertical with its own users.", ""]
    for row in state.values():
        lines += [f"## {row['display']}  (`{row['tenant_name']}`)",
                  f"- tenant id: `{row['tenant_id']}`",
                  f"- workspace: `{row['workspace']}`",
                  f"- packs: {', '.join(row['packs'])}", "", "| login email | role |",
                  "|---|---|",
                  f"| admin@{row['short']}.windrose | Tenant Admin (author) |",
                  f"| approver@{row['short']}.windrose | Tenant Admin (four-eyes approver) |"]
        lines += [f"| {u['email']} | {u['role']} |" for u in row["users"]]
        lines.append("")
    with open(REPORT_MD, "w") as f:
        f.write("\n".join(lines))
    ok(f"cheat sheet written: {REPORT_MD}")


# ---- the full single-tenant onboarding flow (also used by onboard script) ---
def onboard_tenant(pack_dir: str, tname: str, display: str,
                   short: str) -> tuple[dict, dict]:
    """Provision tenant -> rbac bootstrap -> install pack(s) -> per-role test
    users. Returns (persona_entries, report_row). Idempotent."""
    say(f"{display}  —  pack {pack_dir} -> tenant {tname}")
    admin_sub = f"user-admin-{short}"
    approver_sub = f"user-approver-{short}"
    tid = ensure_tenant(tname, display, f"admin@{short}.windrose")
    ws = ensure_rbac_seeded(tid)
    bootstrap_admins(tid, [admin_sub, approver_sub])
    admin_tok = c.user_token(admin_sub, tid, ["*"], workspace_id=ws)
    caps, _, admin = poll_caps(admin_tok)
    if not (admin or caps):
        die(f"tenant admin capabilities never materialized for {tname}")
    ok(f"tenant admin live (admin={admin}, caps={'*' if admin else len(caps)})")

    client = tenant_client(tid, ws, admin_sub, approver_sub)
    installed = []
    if pack_dir in LIBRARY_CONSUMERS:
        say(f"layering {LIBRARY_PACK} (BRD-31 library) into {tname} first")
        installed.append(install_pack(LIBRARY_PACK, client)["pack"])
    installed.append(install_pack(pack_dir, client)["pack"])

    roles = pack_roles(pack_dir)
    if pack_dir in LIBRARY_CONSUMERS:
        roles = pack_roles(LIBRARY_PACK) + roles
    say(f"creating {len(roles)} per-role test users via rbac's member API")
    users = ensure_role_users(tid, ws, admin_tok, roles, short)

    entries = {
        f"admin@{short}.windrose": {"sub": admin_sub, "tenantId": tid,
                                    "workspaceId": ws, "scopes": ADMIN_SCOPES},
        f"approver@{short}.windrose": {"sub": approver_sub, "tenantId": tid,
                                       "workspaceId": ws, "scopes": ADMIN_SCOPES},
    }
    for u in users:
        entries[u["email"]] = {"sub": u["sub"], "tenantId": tid,
                               "workspaceId": ws, "scopes": u["scopes"]}
    row = {"tenant_name": tname, "display": display, "short": short,
           "tenant_id": tid, "workspace": ws, "packs": installed, "users": users}
    return entries, row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="tenant name (e.g. wr-aml) — do just this one")
    ap.add_argument("--no-restart-ui", action="store_true",
                    help="do not restart ui-web (logins go live on next UI boot)")
    args = ap.parse_args()

    plan = [p for p in PACKS if not args.only or p[1] == args.only]
    if not plan:
        die(f"--only {args.only!r} matches no tenant (have: "
            f"{', '.join(p[1] for p in PACKS)})")

    persona_entries: dict = {}
    report_rows: list[dict] = []
    for pack_dir, tname, display, short in plan:
        entries, row = onboard_tenant(pack_dir, tname, display, short)
        persona_entries.update(entries)
        report_rows.append(row)

    merge_personas(persona_entries)
    write_report(report_rows)
    if not args.no_restart_ui:
        restart_ui()

    print()
    say(f"{G}done{N} — {len(report_rows)} tenant(s); logins in MULTITENANT_LOGINS.md")
    for row in report_rows:
        print(f"  {row['display']:34s} admin@{row['short']}.windrose "
              f"+ {len(row['users'])} role user(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
