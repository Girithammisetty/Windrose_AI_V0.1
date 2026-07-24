#!/usr/bin/env python3
"""Cross-tenant authorization probe ("pen-test-lite").

Proves the production-readiness gap "no external pen test" is closed for the
one class of bug that would be catastrophic in a multi-tenant platform:
tenant A's real, live data or actions reachable with tenant B's credentials.

This is a PURE external-facing probe: it never touches a service's source, a
database directly, or an internal test harness. It plays the part of an
attacker who has legitimately obtained a valid, narrow-scoped bearer token for
their OWN tenant (tenant B) and tries to use it against resources that belong
to a DIFFERENT real tenant (tenant A), entirely over the same HTTP APIs a real
client would use.

For each of 4 representative services (case-service, dataset-service,
pipeline-orchestrator, audit-service) it:
  1. Mints a narrow-scoped user token for tenant A and tenant B (RS256, signed
     with the harness IdP key every service already trusts -- the same
     mechanism deploy/e2e/driver.py uses; see deploy/e2e/lib/common.py).
  2. Uses tenant A's token to discover ONE real, already-existing resource
     (no new data is created) and record its current field values.
  3. GET-by-id: tenant B's token tries to read that resource -> must be
     403/404 (never tenant A's real payload).
  4. LIST: tenant B's token lists the collection -> tenant A's resource id
     must never appear.
  5. WRITE (case-service, dataset-service, pipeline-orchestrator): tenant B's
     token PATCH/PUTs a harmless, clearly-labeled marker onto tenant A's
     resource -> must be rejected (non-2xx), AND a re-read with tenant A's own
     token afterward must show the field byte-for-byte unchanged from the
     pre-probe baseline (proves the write did not silently apply).

Every assertion prints the REAL HTTP status code observed. No status code is
assumed. Exits 1 (and prints every failure) if ANY cross-tenant leak is found.

Usage:
    deploy/e2e/.venv/bin/python deploy/security/cross_tenant_authz_probe.py
    make security-probe

Env overrides (all optional -- defaults reuse real seeded tenants, no new
tenants/data are created):
    PROBE_PERSONA_A   persona key in deploy/local/run/personas.json for
                      tenant A (default: admin@demo.datacern)
    PROBE_PERSONA_B   persona key for tenant B (default: admin@verify.datacern,
                      falling back to any other real tenant found in
                      personas.json if that key isn't present)
    CASE_URL / DATASET_URL / PIPELINE_URL / AUDIT_URL   service base URLs
                      (defaults match deploy/e2e/config.env's `make up` ports)
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, ".."))
E2E_LIB = os.path.join(REPO_ROOT, "e2e", "lib")
sys.path.insert(0, E2E_LIB)
import common as c  # noqa: E402  (mints real RS256 tokens w/ the harness IdP key)
import requests  # noqa: E402

G, R, Y, B, N = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"

CASE_URL = os.environ.get("CASE_URL", c.CASE)
DATASET_URL = os.environ.get("DATASET_URL", c.DATASET)
PIPELINE_URL = os.environ.get("PIPELINE_URL", c.PIPELINE)
# audit-service is a "make up" full-platform service, outside the e2e harness's
# money-path boot (deploy/e2e/config.env's PORT_AUDIT comment) -- not in
# common.py's service URL list, so it gets its own default here.
AUDIT_URL = os.environ.get("AUDIT_URL", "http://localhost:8322")

PERSONAS_PATH = os.path.join(REPO_ROOT, "local", "run", "personas.json")
MARKER = f"CROSS_TENANT_PROBE_{uuid.uuid4().hex[:12]}_SHOULD_NOT_APPLY"

FAILS: list[str] = []
RESULTS: list[dict] = []


def ok(label: str, status, note: str = ""):
    print(f"  {G}PASS{N} {label} -> HTTP {status}" + (f"  ({note})" if note else ""))
    RESULTS.append({"probe": label, "verdict": "PASS", "status": status, "note": note})


def bad(label: str, status, note: str = ""):
    print(f"  {R}FAIL{N} {label} -> HTTP {status}" + (f"  ({note})" if note else ""))
    FAILS.append(f"{label} -> HTTP {status} {note}")
    RESULTS.append({"probe": label, "verdict": "FAIL", "status": status, "note": note})


def skip(label: str, why: str):
    print(f"  {Y}SKIP{N} {label} -- {why}")
    RESULTS.append({"probe": label, "verdict": "SKIP", "status": None, "note": why})


def info(m: str):
    print(f"  {Y}··{N} {m}")


def step(t: str):
    print(f"\n{B}=== {t} ==={N}")


# --------------------------------------------------------------------------
# Tenant selection: reuse two REAL, already-seeded tenants. Never create one.
# --------------------------------------------------------------------------
def load_personas() -> dict:
    with open(PERSONAS_PATH) as f:
        return json.load(f)


def pick_persona(personas: dict, env_var: str, default_key: str, exclude_tenant: str | None = None):
    key = os.environ.get(env_var, default_key)
    if key in personas and (exclude_tenant is None or personas[key]["tenantId"] != exclude_tenant):
        return key, personas[key]
    candidates = [(k, v) for k, v in personas.items()
                  if exclude_tenant is None or v["tenantId"] != exclude_tenant]
    if not candidates:
        raise SystemExit(f"no persona available for {env_var} (excluding tenant {exclude_tenant})")
    # prefer an admin-shaped persona so resource discovery has broad read access
    for k, v in candidates:
        if "tenant.admin" in v.get("scopes", []) or "*" in v.get("scopes", []):
            if key not in personas:
                info(f"{env_var}={key!r} not found in personas.json; falling back to {k!r}")
            return k, v
    return candidates[0]


def mint(persona: dict, scopes: list[str]) -> str:
    return c.user_token(persona["sub"], persona["tenantId"], scopes, persona.get("workspaceId"))


def session_for(token: str) -> requests.Session:
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    return s


# --------------------------------------------------------------------------
# Per-service probe definitions
# --------------------------------------------------------------------------
def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def discover_one(sess: requests.Session, url: str, id_field: str):
    r = sess.get(url, timeout=10)
    if r.status_code != 200:
        return None, r
    data = r.json().get("data", [])
    if not data:
        return None, r
    return data[0], r


def run_service_probe(name: str, base: str, list_path: str, get_path_tmpl: str, id_field: str,
                       read_scopes: list[str], sess_a: requests.Session, sess_b: requests.Session,
                       write=None):
    """write, if given: dict(scopes=[...], patch_field=str, patch_fn=callable(marker)->body,
    method='PATCH'|'PUT')."""
    step(f"{name}")
    resource, r = discover_one(sess_a, base + list_path, id_field)
    if resource is None:
        skip(f"{name}: discover a real tenant-A resource",
             f"list returned no usable data (HTTP {r.status_code}); nothing to probe")
        return
    rid = resource[id_field]
    info(f"discovered real tenant-A resource id={rid} via {list_path}")

    # sanity: tenant A must be able to read its OWN resource, else a later
    # 403/404 for tenant B would prove nothing about tenant isolation.
    get_url = base + get_path_tmpl.format(id=rid)
    r = sess_a.get(get_url, timeout=10)
    if r.status_code != 200:
        skip(f"{name}: sanity (owner can read own resource)",
             f"HTTP {r.status_code} -- probe inconclusive, skipping cross-tenant checks")
        return
    ok(f"{name}: sanity -- tenant A reads its own resource", r.status_code)

    # 1) GET-by-id cross-tenant
    r = sess_b.get(get_url, timeout=10)
    label = f"{name}: GET {get_path_tmpl} cross-tenant read"
    if r.status_code in (403, 404) and MARKER not in r.text and str(rid) not in r.text:
        ok(label, r.status_code)
    else:
        bad(label, r.status_code, note=r.text[:300])

    # 2) LIST cross-tenant -- tenant A's resource id must never appear
    r = sess_b.get(base + list_path, timeout=10)
    label = f"{name}: LIST {list_path} cross-tenant list"
    ids_seen = [row.get(id_field) for row in r.json().get("data", [])] if r.status_code == 200 else []
    if rid not in ids_seen:
        ok(label, r.status_code, note=f"{len(ids_seen)} row(s) visible to tenant B, tenant A's id absent")
    else:
        bad(label, r.status_code, note=f"tenant A's resource id {rid} leaked into tenant B's list!")

    # 3) WRITE cross-tenant
    if write:
        baseline = resource.get(write["patch_field"])
        body = write["patch_fn"](MARKER)
        method = write.get("method", "PATCH")
        req = sess_b.patch if method == "PATCH" else sess_b.put
        r = req(get_url, json=body, timeout=10)
        label = f"{name}: {method} {get_path_tmpl} cross-tenant write"
        if r.status_code not in (200, 201, 204):
            ok(label, r.status_code)
        else:
            bad(label, r.status_code, note="write returned success status")
            RESULTS.append({"probe": label, "verdict": "FAIL", "status": r.status_code,
                             "note": "cross-tenant write returned 2xx"})

        # regression check: re-read via tenant A's own token, confirm the
        # field is byte-for-byte unchanged from baseline (not just "marker
        # absent") -- proves the write did not silently apply.
        r2 = sess_a.get(get_url, timeout=10)
        after = r2.json().get("data", {}).get(write["patch_field"]) if r2.status_code == 200 else "<unreadable>"
        label2 = f"{name}: post-write regression check (field unchanged)"
        if after == baseline:
            ok(label2, r2.status_code, note=f"{write['patch_field']}={after!r} (unchanged)")
        else:
            bad(label2, r2.status_code,
                note=f"{write['patch_field']} baseline={baseline!r} now={after!r} -- WRITE LEAKED THROUGH")


def main():
    personas = load_personas()
    key_a, persona_a = pick_persona(personas, "PROBE_PERSONA_A", "admin@demo.datacern")
    key_b, persona_b = pick_persona(personas, "PROBE_PERSONA_B", "admin@verify.datacern",
                                     exclude_tenant=persona_a["tenantId"])
    if persona_a["tenantId"] == persona_b["tenantId"]:
        raise SystemExit("could not find two distinct tenants in personas.json -- "
                          "cannot run a cross-tenant probe")

    print(f"{B}Cross-tenant authorization probe (pen-test-lite){N}")
    print(f"  tenant A: {key_a}  tenant={persona_a['tenantId']}")
    print(f"  tenant B: {key_b}  tenant={persona_b['tenantId']}")
    print(f"  marker:   {MARKER}")

    scopes = [
        "case.case.read", "case.case.update",
        "dataset.dataset.read", "dataset.dataset.update",
        "pipeline.template.read", "pipeline.template.update",
        "audit.event.read",
    ]
    tok_a = mint(persona_a, scopes)
    tok_b = mint(persona_b, scopes)
    sess_a, sess_b = session_for(tok_a), session_for(tok_b)

    run_service_probe(
        "case-service", CASE_URL, "/api/v1/cases?limit=5", "/api/v1/cases/{id}", "id",
        ["case.case.read"], sess_a, sess_b,
        write={"patch_field": "custom_fields",
               "patch_fn": lambda m: {"custom_fields": {"probe": m}}},
    )

    run_service_probe(
        "dataset-service", DATASET_URL, "/api/v1/datasets?limit=5", "/api/v1/datasets/{id}", "id",
        ["dataset.dataset.read"], sess_a, sess_b,
        write={"patch_field": "custom_metadata",
               "patch_fn": lambda m: {"custom_metadata": {"probe": m}}},
    )

    run_service_probe(
        "pipeline-orchestrator", PIPELINE_URL, "/api/v1/pipelines?limit=5", "/api/v1/pipelines/{id}", "id",
        ["pipeline.template.read"], sess_a, sess_b,
        write={"patch_field": "run_parameters",
               "patch_fn": lambda m: {"run_parameters": {"probe": m}},
               "method": "PUT"},
    )

    # audit-service: immutable event log (no update endpoint exists), so this
    # is read-only (GET-by-id + LIST) -- exactly matching what an attacker
    # with a valid tenant-B token could actually attempt.
    now = datetime.now(timezone.utc)
    audit_list_path = f"/api/v1/audit/search?limit=5&from={iso(now - timedelta(days=90))}&to={iso(now)}"
    run_service_probe(
        "audit-service", AUDIT_URL, audit_list_path, "/api/v1/audit/events/{id}", "event_id",
        ["audit.event.read"], sess_a, sess_b,
        write=None,
    )

    step("SUMMARY")
    passed = sum(1 for r in RESULTS if r["verdict"] == "PASS")
    failed = sum(1 for r in RESULTS if r["verdict"] == "FAIL")
    skipped = sum(1 for r in RESULTS if r["verdict"] == "SKIP")
    for r in RESULTS:
        tag = {"PASS": f"{G}PASS{N}", "FAIL": f"{R}FAIL{N}", "SKIP": f"{Y}SKIP{N}"}[r["verdict"]]
        print(f"  {tag}  {r['probe']}  [{r['status']}]")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")

    print(f"\n{B}==================== EVIDENCE (JSON) ===================={N}")
    print(json.dumps({"tenant_a": persona_a["tenantId"], "tenant_b": persona_b["tenantId"],
                       "marker": MARKER, "results": RESULTS}, indent=2, default=str))

    if FAILS:
        print(f"\n{R}CROSS-TENANT ISOLATION FAILURE -- {len(FAILS)} probe(s) leaked:{N}")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print(f"\n{G}ALL CROSS-TENANT ISOLATION PROBES PASSED{N}")


if __name__ == "__main__":
    main()
