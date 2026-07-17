#!/usr/bin/env python3
"""Platform-only boot seed for `make up` (Rule 3: seeding has platform levels).

Produces a working tenant with a real, differentiated, durable RBAC projection
for the four demo personas — and NOTHING vertical-specific. No claims CSV, no
semantic model, no dashboard, no cases, no retrain. Any product vertical
(claims triage today, others later) seeds itself on top of this via its own
script (see seed_claims_demo.py) or, in a real deployment, an Admin does it by
hand through the product UI (Rule 4) — this file's job ends at "a tenant + its
admin/manager/analyst/builder personas can log in and see a correctly gated,
empty UI".

Reuses the real e2e driver machinery (deploy/e2e/driver.py) to drive REAL
APIs — nothing here is faked; the harness only plays the human/operator role
for the parts a human would otherwise click through (accepting an invite,
picking a role).
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

import psycopg  # rbac grant path: durable member rows (present in deploy/e2e/.venv)

E2E_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "e2e")
sys.path.insert(0, E2E_DIR)
sys.path.insert(0, os.path.join(E2E_DIR, "lib"))

import common as c  # noqa: E402
import driver as d  # noqa: E402  (module-level load of TENANT/WORKSPACE + all helpers)

G, Y, B, N = "\033[32m", "\033[33m", "\033[36m", "\033[0m"


def say(m):
    print(f"{B}==>{N} {m}")


def ok(m):
    print(f"  {G}ok{N} {m}")


def warn(m):
    print(f"  {Y}!!{N} {m}")


TENANT = d.TENANT
WORKSPACE = d.WORKSPACE
rds = d.rds

# ---- personas: emails the UI login maps to real tenant/workspace/scopes ----
PERSONA_SCOPES = [
    "case.case.read", "case.case.write", "case.disposition.read", "case.disposition.write",
    "dataset.dataset.read", "dataset.profile.read",
    "experiment.experiment.read", "experiment.model.read",
    "chart.dashboard.read", "usage.report.read",
    "agent.proposal.read", "agent.proposal.decide", "agent.run.create",
]
PERSONAS = {
    "adjuster@demo.windrose": {"sub": "user-adjuster", "role": "adjuster"},
    "manager@demo.windrose": {"sub": "user-manager", "role": "manager"},
    "datascientist@demo.windrose": {"sub": "user-datascientist", "role": "datascientist"},
    "admin@demo.windrose": {"sub": "user-admin", "role": "admin"},
}


def persona_scopes(role):
    """PERSONA_SCOPES plus the raw JWT scope(s) agent-runtime's kill-switch
    routes check directly (app/api/auth.py is_tenant_admin/is_operator) —
    agent-runtime predates the rbac action-catalog convention every other
    service uses, so it is NOT reachable via rbac capabilities/roles at all,
    only this literal scope string. Without this, the admin persona's REAL
    ui-web session could never actually create/lift a kill switch (only the
    e2e harness's synthetic superadmin_token() could), even though the UI
    control is admin-gated and visible. tenant.admin is scoped to the
    persona's own tenant (not "operator", which additionally allows
    cross-tenant/platform-wide kills — out of scope for a demo tenant admin)."""
    if role == "admin":
        return PERSONA_SCOPES + ["tenant.admin"]
    return PERSONA_SCOPES

# every action the UI's BFF fan-out can trigger on a Python-scheme service
# (Python services authorize via the authz:proj single-key projection this
# seeds; Go services use the perm:* projection materialized from rbac grants).
PY_ACTIONS = [
    "dataset.dataset.read", "dataset.profile.read", "dataset.dataset.list",
    "experiment.experiment.read", "experiment.model.read", "experiment.experiment.list",
    "agent.proposal.read", "agent.proposal.decide", "agent.proposal.list", "agent.run.create",
    "eval.suite.read", "inference.job.read",
    # semantic-service is a Python-scheme service: the chart editor reads models
    # (semantic.model.read/list) and rendering compiles metrics (semantic.compile.execute).
    "semantic.model.read", "semantic.model.list", "semantic.compile.execute",
]


# ---- per-persona authorization via rbac's REAL role/grant path -------------
# The projection the UI capability gate reads (rbac GET /me/capabilities) is the
# Go multi-key scheme (perm:*). rbac-service OWNS that projection: it materializes
# perm:* from Postgres grants (group memberships -> roles -> role_actions) via its
# recompute worker. Writing perm:* directly to Redis (as the prior seed did) is
# NOT durable — the worker recomputes each user from SQL ground truth on any grant
# change / rebuild / refresh-on-read, and with no member rows the projection was
# EMPTY, so the UI capability gate failed closed (only Home + Copilot shown).
#
# The fix: give each persona REAL group memberships through rbac's API. The role
# each membership carries is bound (in rbac's seed/roles_actions.yaml) to exactly
# the action strings the ui-web registry gates on, so the worker materializes a
# differentiated, durable perm:* projection that STAYS populated across recomputes.
#
# persona role -> rbac system permission group(s) (one per system role).
#
# datascientist also gets "Use case Admin" (semantic.model.create/update/approve
# live only on Admin + Use case Admin, seed/roles_actions.yaml) so the semantic-
# model builder's four-eyes review (author != approver, SEM-FR-007) is walkable
# by two DISTINCT real demo personas (admin + datascientist), not just admin
# self-blocked from approving its own submissions.
ROLE_GROUPS = {
    "adjuster": ["Case Analyst"],
    "manager": ["Case Manager"],
    "datascientist": ["Model Builder", "Data User", "Use case Admin"],
    "admin": ["Admin"],
}

# Minimum capabilities each persona's /me/capabilities MUST contain — the exact
# action strings ui-web/src/lib/authz/registry.ts gates that persona's nav on
# (admin short-circuits to the "*" wildcard). Verified live after seeding.
REQUIRED_CAPS = {
    "adjuster": ["case.case.read", "ai.proposal.read", "chart.dashboard.read"],
    "manager": ["case.case.read", "ai.proposal.read", "chart.dashboard.read", "usage.report.read"],
    "datascientist": ["dataset.dataset.list", "experiment.experiment.read", "chart.dashboard.read"],
    "admin": ["*"],
}


def seed_python_scheme(user):
    """FALLBACK ONLY (loudly logged): permissive Python single-key projection
    (authz:proj:*) for one persona. The REAL path is: role grants (Postgres)
    -> rbac's projection worker -> authz:proj:* keys with TRUTHFUL facts —
    verified by verify_python_projection() after seed_persona_grants(). This
    fake-admin seeding runs ONLY when that real path fails to materialize, so
    a hands-on demo stays usable while the failure is investigated."""
    for action in PY_ACTIONS:
        for ws, scoped in (("", False), (WORKSPACE, True)):
            facts = {
                "action_known": True, "action_scoped": scoped, "autonomous_enabled": True,
                "flags": {"found": True, "admin": True, "ws_admin": [WORKSPACE]},
                "tenant_actions": {"found": True, "actions": [action]},
                "workspace": {"assigned": True, "actions": [action], "archived": False},
                "resource": {"found": True, "level": "owner", "archived": False},
                "workspace_archived_tenant": False,
            }
            rds.set(f"authz:proj:{TENANT}:{user}:{action}:{ws}", json.dumps(facts))


def retire_legacy_permissive_keys(sub):
    """Remove UN-VERSIONED authz:proj keys for a persona — the permissive
    admin facts an older seed wrote directly (rds.set, no "v" field). The
    projector's keys are versioned and index-GC'd; the legacy ones would
    otherwise mask deny-by-default forever (they carry no TTL). Only runs
    once the REAL path has verified, so it can never lock a demo out."""
    removed = 0
    for key in rds.scan_iter(f"authz:proj:{TENANT}:{sub}:*"):
        try:
            facts = json.loads(rds.get(key) or b"{}")
        except Exception:
            facts = {}
        if "v" not in facts:  # projector keys always carry the version header
            rds.delete(key)
            removed += 1
    return removed


def verify_python_projection(sub="user-datascientist", action="semantic.model.read",
                             tries=40, delay=0.5):
    """Poll for the REAL Python-scheme projection: rbac's worker dual-writes
    authz:proj:{tenant}:{sub}:{action}:{ws} from the same grants that feed
    perm:*. Returns the facts dict when materialized (truthful, versioned),
    else None."""
    key = f"authz:proj:{TENANT}:{sub}:{action}:{WORKSPACE}"
    for _ in range(tries):
        raw = rds.get(key)
        if raw:
            try:
                facts = json.loads(raw)
            except Exception:
                facts = None
            if facts and facts.get("action_known") and not facts.get("deleted"):
                return facts
        time.sleep(delay)
    return None


# rbac's own Postgres (grant ground truth). rbac's projection worker materializes
# perm:* from these rows; the app connects here with the same creds the service uses.
RBAC_DSN = os.environ.get("RBAC_DATABASE_URL", "postgres://{u}:{pw}@{h}:{p}/rbac".format(
    u=os.environ.get("PGUSER", "windrose"), pw=os.environ.get("PGPASSWORD", "windrose_dev"),
    h=os.environ.get("PGHOST", "localhost"), p=os.environ.get("PGPORT", "5432")))


def _ensure_tenant_seeded():
    """Ensure the tenant's system permission groups (one per system role) + the
    default public workspace exist, via rbac's REAL admin seed API (idempotent,
    super-admin scoped — RequireSuperAdmin, which a super-admin token satisfies)."""
    su = c.superadmin_token()
    r = d.req("POST", f"{c.RBAC}/api/v1/admin/tenants/{TENANT}/seed", su, headers=d.J(), json={})
    if r.status_code == 200:
        ok(f"tenant system groups + default workspace ensured: {r.json()}")
    else:
        warn(f"tenant seed: {r.status_code} {r.text[:160]}")


def _resolve_rbac_default_workspace():
    """Return the id of rbac's real 'Default use case' workspace for TENANT.

    rbac creates that workspace with a freshly-generated uuid; driver.WORKSPACE is
    a fabricated uuid5 ("claims-triage-ws-"+tenant) that NEVER matches it. Persona
    tokens and demo data must carry the SAME id the rbac projection worker keys
    workspace-scoped grants under (perm:{tenant}:{sub}:ws:{id}), or every
    workspace-scoped authz check (e.g. pipeline.*) denies even though the grant
    exists. Align to the real row so token == grant-workspace == data-workspace."""
    with psycopg.connect(RBAC_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM workspaces WHERE tenant_id = %s AND lower(name) = lower(%s)",
            (TENANT, "Default use case"))
        row = cur.fetchone()
    return str(row[0]) if row else None


def _align_workspace_to_rbac():
    """Ensure rbac's tenant bootstrap has run, then point WORKSPACE (used for the
    persona token workspace_id AND all demo data) at rbac's real default workspace.
    Must run before anything consumes WORKSPACE (python projection, personas env,
    data seeding)."""
    global WORKSPACE
    _ensure_tenant_seeded()
    real_ws = _resolve_rbac_default_workspace()
    if real_ws and real_ws != WORKSPACE:
        say(f"aligning demo workspace to rbac's real default workspace {real_ws} "
            f"(was fabricated {WORKSPACE}) so workspace-scoped authz resolves")
        WORKSPACE = real_ws
        d.WORKSPACE = real_ws
    elif not real_ws:
        warn("could not resolve rbac default workspace; leaving fabricated WORKSPACE "
             "(workspace-scoped authz may fail)")


def _poll_capabilities(tok, tries=40, delay=0.5):
    """Poll rbac /me/capabilities until the worker has materialized a non-empty
    projection for this subject (recompute SLO is <=5s; we allow ~20s)."""
    for _ in range(tries):
        r = d.req("GET", f"{c.RBAC}/api/v1/me/capabilities", tok)
        if r.status_code == 200:
            b = r.json()
            caps = b.get("capabilities", []) or []
            if caps:
                return caps, b.get("roles", []) or [], bool(b.get("admin"))
        time.sleep(delay)
    return [], [], False


def verify_persona_capabilities(label="verify"):
    """Mint each persona token and assert rbac /me/capabilities is non-empty,
    differentiated, and contains the exact action strings the ui-web registry
    gates that persona's nav on. Returns True iff every persona passes."""
    say(f"[{label}] rbac GET /me/capabilities per persona "
        "(non-empty, differentiated, registry-aligned)")
    all_ok = True
    seen = {}
    for email, p in PERSONAS.items():
        sub, role = p["sub"], p["role"]
        ptok = c.user_token(sub, TENANT, persona_scopes(role), workspace_id=WORKSPACE)
        caps, roles, admin = _poll_capabilities(ptok)
        seen[role] = tuple(sorted(caps))
        need = REQUIRED_CAPS[role]
        if admin and "*" in caps:
            ok(f"{role:13s} sub={sub:18s} admin=* roles={roles}")
            continue
        missing = [a for a in need if a not in caps]
        if caps and not missing:
            ok(f"{role:13s} sub={sub:18s} caps={len(caps)} roles={roles} "
               f"(has {', '.join(need)})")
        else:
            warn(f"{role:13s} sub={sub:18s} MISSING {missing or 'ALL (empty)'}; got {caps}")
            all_ok = False
    # differentiation: no two non-admin personas share an identical capability set
    nonadmin = {r: cs for r, cs in seen.items() if r != "admin"}
    if len(set(nonadmin.values())) != len(nonadmin):
        warn("personas are NOT differentiated (identical capability sets)")
        all_ok = False
    else:
        ok("personas are differentiated (adjuster != manager != datascientist != admin)")
    return all_ok


def seed_persona_grants():
    """Grant each persona its authorization through rbac's REAL role/grant path,
    then let rbac's worker materialize the durable perm:* projection and verify
    it live.

    Members are written as the same Postgres rows rbac's AddMember writes (rbac's
    own action-gated member API can't be bootstrapped: RequireAction reads SQL
    ground truth, and no admin member exists yet). The recompute is then triggered
    through rbac's REAL super-admin rebuild endpoint, so the worker recomputes
    every known user (now incl. the personas) from SQL and materializes perm:*.
    Because the projection is derived from durable grants, it STAYS populated
    across every subsequent recompute / rebuild / refresh-on-read."""
    say("granting persona authorization via rbac's REAL role/grant path "
        "(Postgres group memberships -> projection worker materializes perm:*)")
    _ensure_tenant_seeded()

    added = 0
    with psycopg.connect(RBAC_DSN, autocommit=True) as conn:
        # Pin RLS to the tenant for this session (matches store.WithTenant).
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
        memberships = [(p["sub"], gname) for email, p in PERSONAS.items()
                       for gname in ROLE_GROUPS[p["role"]]]
        # Harness-operator bootstrap THROUGH the real grant path: the driver's
        # MANAGER/APPROVER subjects become real Admin members, so rbac's worker
        # materializes their perm:* AND authz:proj:* projections truthfully
        # (admin=true because they really hold the Admin role) instead of the
        # driver's raw-Redis bootstrap being the only thing authorizing them.
        memberships += [(d.MANAGER, "Admin"), (d.APPROVER, "Admin")]
        for sub, gname in memberships:
            row = conn.execute(
                "SELECT id FROM groups WHERE tenant_id = %s AND group_type = 'permission' "
                "AND lower(name) = lower(%s)", (TENANT, gname)).fetchone()
            if not row:
                warn(f"permission group {gname!r} missing for tenant {TENANT}")
                continue
            cur = conn.execute(
                "INSERT INTO members (id, tenant_id, group_id, user_id) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (group_id, user_id) DO NOTHING",
                (str(uuid.uuid4()), TENANT, row[0], sub))
            if cur.rowcount:
                added += 1
    ok(f"{added} group membership(s) written (durable Postgres rows, "
       "identical to rbac's AddMember path; personas + harness operators)")

    # Trigger rbac's REAL projection recompute (super-admin rebuild endpoint):
    # MarkTenantDirty enqueues every known user; the running worker recomputes
    # each persona from SQL ground truth and writes perm:*.
    su = c.superadmin_token()
    rr = d.req("POST", f"{c.RBAC}/api/v1/admin/projection/rebuild?tenant={TENANT}",
               su, headers=d.J(), json={})
    if rr.status_code in (200, 202):
        ok(f"projection rebuild enqueued via rbac admin API: {rr.json()}")
    else:
        warn(f"projection rebuild: {rr.status_code} {rr.text[:160]}")

    ok_now = verify_persona_capabilities("post-seed")
    # Prove durability: after a worker cycle (>5s SLO) the caps are NOT clobbered.
    say("waiting ~10s for a projection-worker cycle, then re-checking durability")
    time.sleep(10)
    ok_stable = verify_persona_capabilities("post-worker-cycle")
    if ok_now and ok_stable:
        ok("perm:* projection is durable and registry-aligned for all four personas")
    else:
        warn("persona capability verification FAILED — see warnings above")
    return ok_now and ok_stable


def write_personas_env(out_path):
    """Emit the WINDROSE_PERSONAS map ui-web's dev-login reads, binding each
    persona email to the REAL tenant + workspace + scopes seeded above."""
    m = {}
    for email, p in PERSONAS.items():
        m[email] = {"sub": p["sub"], "tenantId": TENANT,
                    "workspaceId": WORKSPACE, "scopes": persona_scopes(p["role"])}
    with open(out_path, "w") as f:
        f.write(json.dumps(m))
    return m


def ensure_platform_seeded():
    """The platform-level boot seed: tenant aligned, four personas with real,
    differentiated, durable RBAC grants, personas.json written for ui-web's dev
    login. Idempotent — safe to call again from a vertical seed script that
    wants to guarantee the platform layer is in place first."""
    _align_workspace_to_rbac()
    print(f"tenant={TENANT}\nworkspace={WORKSPACE}\n")

    # HARNESS BOOTSTRAP (not the product path): the driver's MANAGER/APPROVER
    # operator projection must exist BEFORE any grants do (chicken-and-egg —
    # rbac's own member API is action-gated, and no admin member exists yet).
    # seed_persona_grants() below also makes these operators REAL Admin
    # members, after which the projector's truthful projection takes over.
    d.seed_projection_admin()
    ok("operator (harness bootstrap) projection seeded — personas use the REAL grant path")

    out_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run", "personas.json")
    os.makedirs(os.path.dirname(out_env), exist_ok=True)
    write_personas_env(out_env)
    ok(f"persona login map written: {out_env}")

    # REAL grant path for the four personas: group memberships -> rbac's worker
    # materializes the durable, differentiated perm:* projection the UI gate
    # reads AND the authz:proj:* single-key projection the Python services
    # read. Verified live (non-empty, differentiated, registry-aligned, durable).
    seed_persona_grants()

    # THE REAL PYTHON-SCHEME PATH: verify the projector dual-wrote authz:proj
    # keys for a persona grant. Fall back to the legacy permissive seeding only
    # if it did not — loudly, so a masked projector regression cannot hide.
    say("verifying rbac's projector materialized the Python authz projection (authz:proj:*)")
    facts = verify_python_projection()
    if facts:
        ok("REAL path live: authz:proj key for user-datascientist semantic.model.read "
           f"(admin={((facts.get('flags') or {}).get('admin'))}, v={facts.get('v')}) "
           "— skipping permissive fallback")
        stale = sum(retire_legacy_permissive_keys(p["sub"]) for p in PERSONAS.values())
        if stale:
            ok(f"retired {stale} legacy permissive (un-versioned) persona authz:proj key(s) "
               "so deny-by-default holds")
    else:
        warn("rbac projector did NOT materialize authz:proj keys within ~20s — "
             "FALLING BACK to PERMISSIVE persona seeding (FAKED admin facts). "
             "The real grants->projector->authz:proj path is broken; check rbac logs.")
        for email, p in PERSONAS.items():
            seed_python_scheme(p["sub"])

    return {"tenant": TENANT, "workspace": WORKSPACE}


def main():
    print(f"{B}Windrose platform seed — tenant + four RBAC-gated personas{N}")
    ensure_platform_seeded()
    print(f"\n{G}platform seed complete{N}")
    print(f"  tenant     : {TENANT}")
    print(f"  workspace  : {WORKSPACE}")
    print(f"  personas   : {', '.join(PERSONAS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
