#!/usr/bin/env python3
"""Claims-vertical demo seed (Rule 4: vertical-specific seeding is separate
from the platform boot seed — see seed_platform.py).

Runs AFTER seed_platform.py (called here directly, idempotent) has provisioned
the tenant and its four personas. Reuses the real e2e driver machinery
(deploy/e2e/driver.py) to drive REAL APIs: ingest the claims CSV ->
auto-registered dataset -> profile; author + publish a `claims_core` semantic
model and an "Claims Insights" dashboard over it; create a queue of OPEN
triage cases from the claim rows (with the non-ASCII claimants and the
duplicate-invoice pairs from the CSV); run the triage copilot on a couple of
cases so PENDING proposals sit in the approval inbox; then (best-effort) drive
one full retrain so a trained+promoted model and resolved-case history exist.

The goal: when a persona logs into the UI they SEE claims to triage and a
proposal to approve. Nothing here is faked — every write goes through the real
service on real infra; the harness only plays the human/operator role.

In a real deployment this whole file is what an Admin would instead do by
hand through the product UI (upload a CSV at Data > Upload, author a semantic
model in the semantic-model builder, build a dashboard in the chart builder).
This script exists so a local `make up`/`make e2e` gets the same rich, walkable
demo deterministically without a human at the keyboard — it deliberately does
NOT run as part of the platform-only boot seed.
"""
from __future__ import annotations

import os
import sys
import time

E2E_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "e2e")
sys.path.insert(0, E2E_DIR)
sys.path.insert(0, os.path.join(E2E_DIR, "lib"))

import common as c  # noqa: E402
import driver as d  # noqa: E402  (module-level load of TENANT/WORKSPACE + all helpers)
import seed_platform as sp  # noqa: E402

G, Y, B, N = "\033[32m", "\033[33m", "\033[36m", "\033[0m"


def say(m):
    print(f"{B}==>{N} {m}")


def ok(m):
    print(f"  {G}ok{N} {m}")


def warn(m):
    print(f"  {Y}!!{N} {m}")


TENANT = d.TENANT

# realistic display projections drawn from the CSV rows (kept in sync with
# deploy/e2e/data/claims.csv). A subset become OPEN triage cases in the queue.
DEMO_CASES = [
    ("CLM-1001", "Zürich Ré", "ACME Auto Body", "INV-5540", "12500.50", "auto", "high",
     "Rear collision repair; possible duplicate of INV-5540"),
    ("CLM-1002", "Zürich Ré", "ACME Auto Body", "INV-5540", "12500.50", "auto", "high",
     "Resubmission of the same invoice INV-5540 (duplicate-invoice suspicion)"),
    ("CLM-1006", "María José Peña", "Bayview Collision", "INV-9955", "27650.00", "auto", "high",
     "Total-loss flood claim; high value, expedited review"),
    ("CLM-1007", "Björk Ólafsdóttir", "RapidDry Restoration", "INV-3312", "8800.75", "property", "medium",
     "Water damage; invoice INV-3312 already seen on CLM-1004 (duplicate)"),
    ("CLM-1010", "Zürich Ré", "ACME Auto Body", "INV-5599", "15900.00", "auto", "medium",
     "Second high-value claim on POL-88213 within 3 weeks"),
    ("CLM-1011", "Giovanni Russo", "StormShield Roofing", "INV-4420", "19200.00", "property", "medium",
     "Roof replacement; itemization sparse, verify scope"),
    ("CLM-1003", "John Alvarez", "Bayview Collision", "INV-9921", "3400.00", "auto", "low",
     "Windshield replacement and calibration; routine"),
    ("CLM-1009", "Fatima Al-Sayed", "MercyCare Clinic", "INV-2201", "540.25", "health", "low",
     "Outpatient physiotherapy; routine low-value claim"),
]


def create_open_cases(dataset_urn):
    say("creating OPEN triage cases in the queue (real case-service -> OpenSearch)")
    tok = d.utok()
    due = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 7 * 86400))
    ids = []
    for (row_pk, name, vendor, inv, amount, ctype, sev, note) in DEMO_CASES:
        body = {"dataset_urn": dataset_urn, "dataset_version": "1", "due_date": due,
                "severity": sev, "workspace_id": d.WORKSPACE,
                "rows": [{"row_pk": row_pk, "display_projection": {
                    "claimant_name": name, "vendor": vendor, "invoice_no": inv,
                    "amount": amount, "claim_type": ctype, "note": note}}]}
        r = d.req("POST", f"{c.CASE}/api/v1/cases", tok, headers=d.J(), json=body)
        if r.status_code in (200, 201):
            created = (r.json().get("data", r.json())).get("created", [])
            if created:
                ids.append(created[0]["id"])
    ok(f"{len(ids)} open triage cases created (incl. duplicate-invoice + non-ASCII claimants)")
    reindex_cases()
    return ids


def reindex_cases():
    """case-service projects cases into OpenSearch via an async Kafka consumer
    (eventually consistent). For a deterministic demo, rebuild the tenant index
    from Postgres (source of truth) via the real admin reindex endpoint so every
    seeded case is immediately searchable in the UI."""
    try:
        r = d.req("POST", f"{c.CASE}/api/v1/admin/reindex", d.utok(), headers=d.J(), json={})
        if r.status_code == 200:
            ok(f"OpenSearch reindex from Postgres: {r.json().get('data', r.json())}")
        else:
            warn(f"reindex: {r.status_code} {r.text[:120]}")
    except Exception as e:
        warn(f"reindex error: {e}")


def run_triage_for_pending_proposals(case_ids, n=2):
    say(f"running the triage copilot on {n} cases -> PENDING proposals in the inbox "
        "(agent-runtime -> ai-gateway -> real Ollama)")
    made = 0
    for cid in case_ids[:n]:
        try:
            pid = d.step_d_triage(cid)  # creates a proposal, does NOT approve it
            if pid:
                made += 1
        except Exception as e:
            warn(f"triage on {cid}: {e}")
    if made:
        ok(f"{made} disposition proposal(s) now awaiting approval in the inbox")
    else:
        warn("no pending proposals produced (copilot/LLM path — see logs)")
    return made


def best_effort_retrain(dataset_urn):
    say("driving ONE retrain (best-effort): corrections -> train -> promote "
        "(pipeline-orchestrator + experiment-service + inference)")
    # The driver's step_* helpers signal failure by appending to d.FAILS (via bad()),
    # NOT by raising — so snapshot the failure list and honour it. A bad()/FAIL result
    # must never be reported as "completed".
    fails_before = len(d.FAILS)
    try:
        experiment_id = d.step_i_pre_experiment()
        n_labeled = d.step_h_corrections(dataset_urn)
        if n_labeled:
            mlflow_run_id = d.step_i_retrain(dataset_urn)
            if mlflow_run_id:
                d.step_j_promote(experiment_id, mlflow_run_id)
                d.step_k_inference(dataset_urn)
    except Exception as e:
        warn(f"retrain path did not fully complete (non-fatal for the UI demo): {e}")
        return False
    new_fails = d.FAILS[fails_before:]
    if new_fails:
        warn(f"retrain path did NOT complete cleanly — {len(new_fails)} "
             f"assertion(s) failed (non-fatal for the UI demo):")
        for f in new_fails:
            warn(f"  - {f}")
        return False
    ok("retrain path completed (trained + promoted model available)")
    return True


# ---- semantic model + insights dashboard (charts over the claims dataset) ----
# Service base URLs (ports per deploy/e2e/config.env). common.py only defines the
# wave-1 service URLs; the analytics-plane ones are read from env with matching
# defaults so the seed works under `make up` and a bare local run alike.
SEMANTIC_URL = os.environ.get("SEMANTIC_URL", "http://localhost:8086")
CHART_URL = os.environ.get("CHART_URL", "http://localhost:8320")
QUERY_URL = os.environ.get("QUERY_URL", "http://localhost:8085")

# semantic-service actions the seed exercises (canonical names: model.write was
# split into model.create / model.update). semantic-service authorizes via the
# single-key OPA projection scheme (authz:proj:*), which rbac's projector now
# dual-writes from real grants — the operator is a real Admin member (see
# seed_platform.seed_persona_grants), so these keys materialize truthfully.
# d.seed_py_authz remains ONLY as a loudly-logged fallback when the real path
# fails.
_SEMANTIC_ACTIONS = [
    "semantic.model.read", "semantic.model.create", "semantic.model.update",
    "semantic.model.approve", "semantic.compile.execute",
]


def _normalized_relation(name: str) -> str:
    """Mirror dataset-service `_safe_relation` EXACTLY (app/api/schemas.py): the
    physical relation query-service materializes `main.<relation>` from. dataset
    name -> lowercased, every run of non-alnum collapsed to one underscore, a
    leading digit prefixed with `t_`."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        slug = "dataset"
    if slug[0].isdigit():
        slug = f"t_{slug}"
    return slug


def _claims_model_definition(dataset_urn: str, relation: str) -> dict:
    """claims_core over the current claims dataset.

    entity.table = main.<relation> compiles (SEM compiler emits
    `FROM {quote_table(entity.table)}`) to `FROM "main"."<relation>"`, the exact
    physical identifier dataset-service /resolve returns and query-service
    auto-materializes. amount is ingested as a STRING, so the numeric measures use
    a `cast(amount as double)` expr (the SEM restricted grammar allows CAST; the
    compiler renders `sum(CAST("c"."amount" AS double))`) — a bare `sum(amount)`
    would error in DuckDB over a varchar column."""
    return {
        "entities": [{
            "name": "claims", "dataset_urn": dataset_urn, "table": f"main.{relation}",
            "primary_key": ["claim_id"], "dataset_version_policy": {"policy": "latest"},
        }],
        "dimensions": [
            {"name": "claim_type", "entity": "claims", "type": "categorical",
             "column": "claim_type"},
            {"name": "vendor", "entity": "claims", "type": "categorical",
             "column": "vendor"},
        ],
        "measures": [
            {"name": "claim_count", "entity": "claims", "agg": "count",
             "description": "Number of claims"},
            {"name": "total_amount", "entity": "claims", "agg": "sum",
             "expr": "cast(amount as double)", "format": "decimal",
             "description": "Total claimed amount"},
            {"name": "avg_amount", "entity": "claims", "agg": "avg",
             "expr": "cast(amount as double)", "format": "decimal",
             "description": "Average claimed amount"},
        ],
    }


def _find_model_by_name(tok, name):
    r = d.req("GET", f"{SEMANTIC_URL}/api/v1/models?filter[workspace_id]={d.WORKSPACE}", tok)
    if r.status_code == 200:
        for m in r.json().get("data", []):
            if m.get("name") == name:
                return m
    return None


def _authoritative_dataset_urn(tok, dataset_name):
    """Return the CURRENT workspace's dataset urn for `dataset_name`.

    The (urn, name) pair passed into seed_charts_demo can diverge in a reused/dirty
    environment (every prior `make up` accumulates datasets and the dev DB role does
    not FORCE row-level security, so cross-tenant rows are visible), which would bind
    the model to a dataset ABSENT from this tenant -> semantic binding validation
    404s. Re-resolving the id by name inside this workspace guarantees the model
    binds to an in-tenant dataset that dataset-service's internal detail resolves."""
    r = d.req("GET", f"{c.DATASET}/api/v1/datasets?workspace_id={d.WORKSPACE}", tok)
    if r.status_code == 200:
        for x in r.json().get("data", []):
            if x.get("name") == dataset_name:
                return f"wr:{TENANT}:dataset:dataset/{x['id']}"
    return None


def _find_dashboard_by_name(tok, name):
    r = d.req("GET", f"{CHART_URL}/api/v1/dashboards?workspace_id={d.WORKSPACE}", tok)
    if r.status_code == 200:
        for db in r.json().get("data", []):
            if db.get("name") == name:
                return db
    return None


def _dashboard_charts(tok, dash_id):
    """name -> chart_id for a dashboard. chart-service has no list-charts GET, so
    enumerate ids via the batch-data endpoint (each result carries chart_id even
    when its own resolve errors) then read each chart's name."""
    out = {}
    r = d.req("POST", f"{CHART_URL}/api/v1/dashboards/{dash_id}/data", tok,
              headers=d.J(), json={})
    if r.status_code == 200:
        for res in (r.json().get("data") or {}).get("results", []):
            cid = res.get("chart_id")
            if not cid:
                continue
            g = d.req("GET", f"{CHART_URL}/api/v1/charts/{cid}", tok)
            if g.status_code == 200:
                out[(g.json().get("data") or {}).get("name")] = cid
    return out


def seed_charts_demo(dataset_urn, dataset_name):
    """Create the `claims_core` semantic model + an Insights dashboard with a bar
    chart (claims by claim_type) and a grid chart over the CURRENT claims dataset,
    then leave them renderable through chart-service GET /charts/{id}/data.

    Idempotent: skips model/dashboard creation when they already exist by name.
    All writes go through the REAL services (semantic-service authoring/review
    workflow, chart-service dashboards/charts) under the operator's admin
    projection — no fakes.
    """
    say("seeding semantic model `claims_core` + Insights dashboard (charts over "
        "the real claims dataset)")
    if not dataset_urn or not dataset_name:
        warn("no claims dataset available; skipping chart demo")
        return {}

    # Authorize the operator on semantic-service. REAL path first: the operator
    # is a real Admin member (seed_platform.seed_persona_grants), so rbac's
    # projector dual-writes truthful admin facts to authz:proj:*. Only if that
    # path failed do we fall back to the harness's permissive seeding — loudly.
    if not sp.verify_python_projection(sub=d.MANAGER, action="semantic.model.create",
                                        tries=10):
        warn("operator authz:proj keys NOT materialized by the rbac projector — "
             "FALLING BACK to permissive harness seeding of semantic actions "
             "(FAKED admin facts; the real grant->projection path is broken)")
        for a in _SEMANTIC_ACTIONS:
            d.seed_py_authz(a)
    write_tok = c.user_token(d.MANAGER, TENANT, ["*"], workspace_id=d.WORKSPACE)
    # A DISTINCT subject is required to approve (four-eyes: the author cannot
    # approve their own version, SEM-FR-007).
    approve_tok = c.user_token(d.APPROVER, TENANT, ["*"], workspace_id=d.WORKSPACE)

    # Bind to the current workspace's dataset authoritatively (see helper) so the
    # model never binds to a stale/cross-tenant id in a reused environment.
    auth_urn = _authoritative_dataset_urn(write_tok, dataset_name)
    if auth_urn and auth_urn != dataset_urn:
        warn(f"rebinding claims_core to the in-workspace dataset urn "
             f"(passed {dataset_urn} -> {auth_urn})")
        dataset_urn = auth_urn
    relation = _normalized_relation(dataset_name)
    definition = _claims_model_definition(dataset_urn, relation)
    say(f"claims dataset {dataset_name!r} -> entity.table main.{relation} "
        f"(compiles to FROM \"main\".\"{relation}\")")

    summary = {"model": "claims_core", "relation": relation}

    # --- semantic model: create -> patch definition -> submit -> approve --------
    model = _find_model_by_name(write_tok, "claims_core")
    if model:
        model_id = model["id"]
        ok(f"claims_core already exists (id={model_id}); ensuring published")
    else:
        r = d.req("POST", f"{SEMANTIC_URL}/api/v1/models", write_tok, headers=d.J(),
                  json={"workspace_id": d.WORKSPACE, "name": "claims_core",
                        "description": "Claims semantic model (claim_type/vendor dims; "
                                       "count + amount measures)",
                        "definition": definition})
        if r.status_code != 201:
            warn(f"model create failed: {r.status_code} {r.text[:200]}")
            return summary
        model_id = r.json()["data"]["id"]
        ok(f"claims_core model created (id={model_id})")
    summary["model_id"] = model_id

    published = bool((model or {}).get("published_version_id"))
    if not published:
        # Ensure the open draft carries our definition, then run the review flow.
        d.req("PATCH", f"{SEMANTIC_URL}/api/v1/models/{model_id}/versions/1",
              write_tok, headers=d.J(), json={"definition": definition})
        rs = d.req("POST", f"{SEMANTIC_URL}/api/v1/models/{model_id}/versions/1/submit",
                   write_tok, headers=d.J(), json={})
        if rs.status_code == 200:
            ra = d.req("POST",
                       f"{SEMANTIC_URL}/api/v1/models/{model_id}/versions/1/approve",
                       approve_tok, headers=d.J(), json={"note": "demo publish"})
            if ra.status_code == 200:
                ok("claims_core published (v1 approved)")
                summary["published"] = True
            else:
                warn(f"approve failed: {ra.status_code} {ra.text[:200]}")
        else:
            # Known blocker: submit runs binding validation that calls
            # dataset-service GET /internal/v1/datasets/{id}, which does not exist
            # (404) -> "dataset not found". Charts are still created so they render
            # as soon as the model is published.
            warn(f"submit/publish blocked (binding validation): {rs.status_code} "
                 f"{rs.text[:200]}")
    else:
        summary["published"] = True

    # --- Insights dashboard + bar chart + grid chart ---------------------------
    dash = _find_dashboard_by_name(write_tok, "Claims Insights")
    if dash:
        dash_id = dash["id"]
        ok(f"Claims Insights dashboard already exists (id={dash_id})")
    else:
        r = d.req("POST", f"{CHART_URL}/api/v1/dashboards", write_tok, headers=d.J(),
                  json={"name": "Claims Insights", "module": "insights",
                        "workspace_id": d.WORKSPACE,
                        "description": "Claims analytics over the ingested dataset",
                        "layout": [], "meta": {}, "tags": ["claims", "demo"]})
        if r.status_code != 201:
            warn(f"dashboard create failed: {r.status_code} {r.text[:200]}")
            return summary
        dash_id = r.json()["data"]["id"]
        ok(f"Insights dashboard created (id={dash_id})")
    summary["dashboard_id"] = dash_id

    display_meta = {"semantic_model": "claims_core", "workspace_id": d.WORKSPACE}
    measure_urn = f"wr:{TENANT}:semantic:measure/claim_count"
    amount_urn = f"wr:{TENANT}:semantic:measure/total_amount"

    existing_charts = _dashboard_charts(write_tok, dash_id)

    def _ensure_chart(name, key, body):
        if name in existing_charts:
            summary[key] = existing_charts[name]
            ok(f"{name!r} chart already exists (id={summary[key]})")
            return
        r = d.req("POST", f"{CHART_URL}/api/v1/dashboards/{dash_id}/charts", write_tok,
                  headers=d.J(), json=body)
        if r.status_code == 201:
            summary[key] = r.json()["data"]["id"]
            ok(f"{name!r} chart created (id={summary[key]})")
        elif r.status_code == 409:  # already exists — reconcile id from the batch list
            summary[key] = _dashboard_charts(write_tok, dash_id).get(name)
            ok(f"{name!r} chart already exists (id={summary[key]})")
        else:
            warn(f"{name!r} chart create failed: {r.status_code} {r.text[:200]}")

    # Bar chart: count of claims by claim_type (family=axis: x dim + y measures).
    _ensure_chart("Claims by type", "bar_chart_id", {
        "name": "Claims by type", "chart_type": "vertical_bar_chart",
        "description": "Count of claims by claim_type",
        "config": {"x": {"dimension": "claim_type"},
                   "y": [{"measure": "claim_count", "agg_fn": "count"}]},
        "display_meta": display_meta,
        "sources": [{"position": 0, "source_type": "semantic_measure",
                     "source_urn": measure_urn}]})

    # Grid chart: claim_type + count + total amount (family=grid: columns[] + the
    # x/y the resolver maps to compile dims/metrics).
    _ensure_chart("Claims grid", "grid_chart_id", {
        "name": "Claims grid", "chart_type": "grid_chart",
        "description": "Claims by type with counts and total amount",
        "config": {"columns": ["claim_type", "claim_count", "total_amount"],
                   "x": {"dimension": "claim_type"},
                   "y": [{"measure": "claim_count", "agg_fn": "count"},
                         {"measure": "total_amount", "agg_fn": "sum"}]},
        "display_meta": display_meta,
        "sources": [{"position": 0, "source_type": "semantic_measure",
                     "source_urn": measure_urn},
                    {"position": 1, "source_type": "semantic_measure",
                     "source_urn": amount_urn}]})

    # --- place both charts on the dashboard grid (populates the UI canvas) -----
    # GET /dashboards/{id} returns a placement-only layout, so the dashboard is
    # visually empty until we PATCH one. Bar top-left, grid to its right; simple
    # non-overlapping 12-col grid. Idempotent: we set the full layout each run.
    placements = []
    if summary.get("bar_chart_id"):
        placements.append({"chart_id": summary["bar_chart_id"], "x": 0, "y": 0, "w": 6, "h": 4})
    if summary.get("grid_chart_id"):
        placements.append({"chart_id": summary["grid_chart_id"], "x": 6, "y": 0, "w": 6, "h": 4})
    if placements:
        rl = d.req("PATCH", f"{CHART_URL}/api/v1/dashboards/{dash_id}", write_tok,
                   headers=d.J(), json={"layout": placements})
        if rl.status_code == 200:
            ok(f"dashboard layout set ({len(placements)} charts placed)")
            summary["layout"] = placements
        else:
            warn(f"dashboard layout PATCH failed: {rl.status_code} {rl.text[:200]}")

    # --- warm EVERY chart's data via the consistent single-chart GET (real rows).
    # chart-service caches the resolved result, so warming here means the first UI
    # dashboard view (which batch-resolves all tiles) is a cache hit and never
    # shows a cold-resolve error. Do it sequentially (the batch fan-out is what
    # stresses a cold cache).
    for name, key in (("Claims by type", "bar_chart_id"), ("Claims grid", "grid_chart_id")):
        cid = summary.get(key)
        if not cid:
            continue
        rd = d.req("GET", f"{CHART_URL}/api/v1/charts/{cid}/data", write_tok)
        if rd.status_code == 200:
            rows = (rd.json().get("data") or {}).get("rows")
            ok(f"{name!r} /data warmed, real rows: {rows}")
            if key == "bar_chart_id":
                summary["bar_rows"] = rows
        else:
            warn(f"{name!r} /data warm failed: {rd.status_code} {rd.text[:150]}")
    return summary


def _find_saved_query_by_name(tok, name):
    r = d.req("GET", f"{QUERY_URL}/api/v1/queries?workspace_id={d.WORKSPACE}", tok)
    if r.status_code == 200:
        for q in r.json().get("data", []):
            if q.get("name") == name:
                return q
    return None


def seed_network_query_demo(dataset_name):
    """Seed one real saved query (vendor -> claim_type edges, real GROUP BY
    counts) over the claims dataset, so the network-chart family (network/
    network_graph/tree/decision_tree chart types — resolved via
    source_type=saved_query, chart-service has no named-measure path for
    them) has a real, ready-to-pick source in the chart builder. Idempotent:
    skips creation when a saved query of this name already exists.
    """
    say("seeding a saved query for the network-chart family (vendor -> claim_type)")
    if not dataset_name:
        warn("no claims dataset available; skipping network-query demo")
        return None

    write_tok = c.user_token(d.MANAGER, TENANT, ["*"], workspace_id=d.WORKSPACE)
    name = "Claims: vendor to claim type"
    existing = _find_saved_query_by_name(write_tok, name)
    if existing:
        ok(f"{name!r} saved query already exists (id={existing['id']})")
        return existing["id"]

    sql = (
        "SELECT vendor AS parent, claim_type AS child, count(*) AS n "
        f"FROM {{{{dataset('{dataset_name}')}}}} GROUP BY vendor, claim_type"
    )
    r = d.req("POST", f"{QUERY_URL}/api/v1/queries", write_tok, headers=d.J(), json={
        "name": name, "description": "Claim counts by vendor and claim_type — "
                                      "demo source for network-family charts",
        "module_names": ["insights"], "workspace_id": d.WORKSPACE,
        "sql_text": sql, "tags": ["claims", "demo"]})
    if r.status_code != 201:
        warn(f"saved query create failed: {r.status_code} {r.text[:200]}")
        return None
    query_id = r.json()["data"]["id"]
    ok(f"{name!r} saved query created (id={query_id})")
    return query_id


def main():
    print(f"{B}Windrose claims demo seed — populating the claims vertical for hands-on testing{N}")
    # Guarantee the platform layer (tenant, personas, RBAC) is in place; a no-op
    # if seed_platform.py already ran (idempotent throughout).
    sp.ensure_platform_seeded()

    # dispositions catalog (claims-vertical taxonomy: duplicate_invoice/approved)
    # + a redundant-but-harmless idempotent rbac tenant-seed call. Shared with
    # the e2e driver so this stays byte-identical to what `make e2e` exercises.
    d.step0_seed()

    dataset_urn = d.step_a_ingest()
    if not dataset_urn:
        warn("ingestion did not yield a dataset_urn; UI will still load but with no cases")
        return 0
    try:
        d.materialize_features(dataset_urn)
    except Exception as e:
        warn(f"materialize_features: {e}")
    d.step_b_dataset(dataset_urn)

    # Semantic model + Insights dashboard/charts over the just-seeded claims
    # dataset (real semantic-service + chart-service; renders via /charts/{id}/data).
    try:
        seed_charts_demo(dataset_urn, d.EVID.get("ingest_dataset_name"))
    except Exception as e:  # non-fatal for the rest of the UI demo
        warn(f"chart demo seeding error: {e}")

    try:
        seed_network_query_demo(d.EVID.get("ingest_dataset_name"))
    except Exception as e:  # non-fatal for the rest of the UI demo
        warn(f"network-query demo seeding error: {e}")

    case_ids = create_open_cases(dataset_urn)
    run_triage_for_pending_proposals(case_ids, n=2)

    if os.environ.get("WINDROSE_SEED_RETRAIN", "1") == "1":
        best_effort_retrain(dataset_urn)
    else:
        say("skipping retrain (WINDROSE_SEED_RETRAIN=0)")

    # final deterministic reindex so every case (incl. any resolved during the
    # retrain corrections) is searchable in the UI.
    reindex_cases()

    print(f"\n{G}claims demo seed complete{N}")
    print(f"  open cases in queue : {len(case_ids)}")
    print(f"  dataset_urn         : {dataset_urn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
