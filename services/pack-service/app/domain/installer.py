"""Install orchestration — the governed, in-cluster promotion of packctl.

pack-service does NOT reinvent materialization: it reuses packctl's proven,
idempotent ``PlatformClient.ensure_*`` calls against Core's real public APIs.
What it ADDS over the packctl CLI is the governed-service envelope: it runs the
install AS THE INSTALLING USER (the user's JWT is forwarded, so every write is
authorized truthfully), persists a durable DB registry + ledger, computes a
dry-run PLAN before applying, origin-tags every materialized object, and can
reverse them on uninstall.

Increment 1 materializes the component kinds a single authorized principal can
create without a distinct four-eyes approver: dispositions, roles, saved
queries, and governed decision tables. Kinds that need a four-eyes approver
(semantic models, verified queries) or a data-ingestion chain (datasets,
dashboards, cases, pipelines) are reported in the plan as ``deferred`` — honest,
never faked — pending the follow-on that gives pack-service a governed approver
identity.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from app.config import Settings
from app.domain import catalog

# Kinds inc1 materializes, in dependency order (dispositions before decision
# tables, whose outcome codes the case-service catalog validates). These are the
# self-contained kinds a single authorized principal creates without a four-eyes
# approver AND without the data-ingestion chain — so they install cleanly on
# their own. (saved_queries/dashboards need the pack's datasets first, which is
# a deferred kind; they're reported `deferred` in the plan, not faked.)
INC1_KINDS = ("dispositions", "case_fields", "case_schemas", "display_labels", "guardrails",
              "agent_configs", "eval_sets", "model_archetypes", "ontology", "write_adapters",
              "connection_templates", "roles", "decision_models")

# inc2 data chain, in dependency order. datasets ingest first; the semantic
# model + verified queries are authored + SUBMITTED as governed drafts (NOT
# approved — a pack must not bypass four-eyes; a distinct human steward approves
# them in the normal review UI). saved queries create against the ingested
# datasets. Dashboards depend on the model's PUBLISHED measure projection, so
# they materialize in the second phase (run_complete), after approval.
INC2_PHASE1_KINDS = ("datasets", "semantic_models", "verified_queries", "saved_queries",
                     "cases", "pipelines", "memories")
INC2_PHASE2_KINDS = ("dashboards",)

# Kinds whose Core service exposes a real revert (delete) verb → reversible on
# uninstall. Others are ledgered + tombstoned honestly (PKG-FR-025): the object
# is retained and loses its pack-origin marker, because Core has no delete verb
# for it yet — a real, surfaced gap in the materialization contract (PKG-FR-030).
REVERSIBLE_KINDS = {"roles", "saved_queries", "dashboards", "case_fields", "case_schemas",
                    "display_labels", "guardrails", "agent_configs", "pipelines",
                    "memories", "model_archetypes", "ontology", "write_adapters",
                    "connection_templates"}


def _packctl_client():
    catalog._packctl()  # ensure packs dir on sys.path
    from packctl.client import Endpoints, PlatformClient  # noqa: PLC0415

    return Endpoints, PlatformClient


def _endpoints(settings: Settings):
    Endpoints, _ = _packctl_client()
    return Endpoints(
        ingestion=settings.ingestion_url, dataset=settings.dataset_url,
        semantic=settings.semantic_url, query=settings.query_url,
        chart=settings.chart_url, case=settings.case_url,
        rbac=settings.rbac_svc_url, agent=settings.agent_url,
        memory=settings.memory_url, pipeline=settings.pipeline_url,
        identity=settings.identity_url, eval=settings.eval_url,
        experiment=settings.experiment_url,
    )


def build_client(settings: Settings, tenant_id: str, workspace_id: str, user_jwt: str):
    """A packctl PlatformClient that authors every write AS the installing user
    (the forwarded JWT is used for all three token roles; inc1 only exercises the
    single-principal author path)."""
    _, PlatformClient = _packctl_client()

    def token() -> str:
        return user_jwt

    return PlatformClient(
        endpoints=_endpoints(settings), tenant_id=tenant_id, workspace_id=workspace_id,
        author_token=token, approver_token=token, agent_token=token,
        log=lambda *_: None,
    )


# ---- dry-run plan -----------------------------------------------------------

def plan(client, manifest) -> list[dict]:
    """Compute what an install WOULD do without any side effect (PKG-FR-020):
    per component, `create` (new) or `exists` (idempotent no-op); kinds inc1
    doesn't materialize are `deferred` with a reason."""
    ops: list[dict] = []
    existing = _existing_names(client)
    materializable = set(INC1_KINDS) | set(INC2_PHASE1_KINDS)
    for comp in manifest.components:
        if comp.kind in INC2_PHASE2_KINDS:
            # Dashboards materialize in phase 2, after a steward publishes the
            # semantic model (their measure projection resolves only then).
            ops.append({"kind": comp.kind, "identity": comp.identity, "action": "after_approval",
                        "detail": "materializes once the semantic model is approved"})
            continue
        if comp.kind not in materializable:
            ops.append({"kind": comp.kind, "identity": comp.identity, "action": "deferred",
                        "detail": "needs a Core write surface not exposed yet"})
            continue
        for name in _component_names(manifest, comp):
            action = "exists" if name in existing.get(comp.kind, set()) else "create"
            ops.append({"kind": comp.kind, "identity": comp.identity, "name": name, "action": action})
    return ops


def _existing_names(client) -> dict[str, set[str]]:
    """The set of already-present object names/codes per kind (idempotency)."""
    ws = client.workspace_id
    out: dict[str, set[str]] = {}

    def names(resp, key) -> set[str]:
        if resp.status_code != 200:
            return set()
        return {str(d.get(key)) for d in (resp.json().get("data") or []) if d.get(key)}

    tok = client.author_token()
    e = client.endpoints
    out["dispositions"] = names(
        client._req("GET", f"{e.case}/api/v1/dispositions?workspace_id={ws}", tok), "code")
    # case-fields derive the workspace from the JWT claim (no query param).
    out["case_fields"] = names(
        client._req("GET", f"{e.case}/api/v1/case-fields", tok), "name")
    # display labels are a per-tenant {key: value} map (identity-service).
    lb = client._req("GET", f"{e.identity}/api/v1/tenants/self/labels", tok)
    out["display_labels"] = set((lb.json().get("labels") or {}).keys()) \
        if lb.status_code == 200 else set()
    # NOTE: cases are deliberately NOT prefetched here. The case-service LIST
    # projection omits row_pk (it is only on the detail view), and idempotency is
    # enforced SERVER-SIDE anyway — case creation dedups on (dataset_urn, row_pk)
    # via the case dedup_key, so re-installing never duplicates a seed case. The
    # plan therefore shows cases as "create" (a materialize intent) and the apply
    # is safe. Verified: 6 installs of ap-invoice-audit → exactly 6 seed cases.
    out["roles"] = names(client._req("GET", f"{e.rbac}/api/v1/roles?limit=200", tok), "name")
    out["saved_queries"] = names(
        client._req("GET", f"{e.query}/api/v1/queries?workspace_id={ws}", tok), "name")
    dm = client._req("GET", f"{e.agent}/api/v1/decision-models", tok)
    out["decision_models"] = {str(d.get("name")) for d in (dm.json().get("data") or [])
                              if dm.status_code == 200 and d.get("workspace_id") == ws
                              and d.get("name")}
    out["datasets"] = names(
        client._req("GET", f"{e.dataset}/api/v1/datasets?workspace_id={ws}", tok), "name")
    sm = client._req("GET", f"{e.semantic}/api/v1/models?filter[workspace_id]={ws}", tok)
    out["semantic_models"] = names(sm, "name")
    out["verified_queries"] = set()  # nl_text is the identity; treat as always-new in the plan
    return out


def _guardrail_envelope(gd: dict, workspace_id: str) -> dict:
    """Build a per-agent security envelope from a pack guardrails entry (BRD 53
    inc2). budget + pii are static; bind_workspace injects the install workspace
    so the agent's grounding reads are confined to it; explicit dataset_urns pass
    through. agent-runtime validates the shape and clamps the budget DOWN to the
    operator platform ceiling (BR-8) — a pack can never raise it."""
    env: dict = {}
    if gd.get("budget") is not None:
        env["budget"] = gd["budget"]
    if gd.get("pii") is not None:
        env["pii"] = gd["pii"]
    scope: dict = {}
    if gd.get("bind_workspace") and workspace_id:
        scope["workspaces"] = [workspace_id]
    if gd.get("dataset_urns"):
        scope["dataset_urns"] = gd["dataset_urns"]
    if scope:
        env["data_scope"] = scope
    return env


def _component_names(manifest, comp) -> list[str]:
    """The human names a component file will create (for the plan)."""
    from packctl.manifest import load_component_file  # noqa: PLC0415

    doc = load_component_file(manifest, comp)
    if comp.kind == "dispositions":
        return [d["code"] for d in doc]
    if comp.kind == "case_fields":
        return [f["name"] for f in doc]
    if comp.kind == "case_schemas":
        return [sc["schema_key"] for sc in doc]
    if comp.kind == "ontology":
        return [e["entity_key"] for e in doc]
    if comp.kind == "write_adapters":
        return [wa["name"] for wa in doc]
    if comp.kind == "connection_templates":
        return [ct["name"] for ct in doc]
    if comp.kind == "display_labels":
        return [lbl["key"] for lbl in doc]
    if comp.kind == "guardrails":
        return [gd["agent_key"] for gd in doc]
    if comp.kind == "agent_configs":
        return [ac["agent_key"] for ac in doc]
    if comp.kind == "eval_sets":
        return [es["dataset_key"] for es in doc]
    if comp.kind == "model_archetypes":
        return [a["archetype_key"] for a in doc]
    if comp.kind == "cases":
        return [r["row_pk"] for r in doc.get("rows", [])]
    if comp.kind == "pipelines":
        return [p["name"] for p in (doc if isinstance(doc, list) else [doc])]
    if comp.kind == "memories":
        # grounding records have no name; the plan shows one create for the set.
        return [comp.identity]
    if comp.kind == "roles":
        return [r["name"] for r in doc]
    if comp.kind == "saved_queries":
        return [q["name"] for q in (doc if isinstance(doc, list) else [doc])]
    if comp.kind == "decision_models":
        return [dm["name"] for dm in (doc if isinstance(doc, list) else [doc])]
    if comp.kind == "datasets":
        return [ds["name"] for ds in (doc if isinstance(doc, list) else [doc])]
    if comp.kind == "semantic_models":
        return [doc["name"]]
    if comp.kind == "verified_queries":
        return [f"{comp.identity}_{i}" for i in range(len(doc))]
    return [comp.identity]


# ---- execute ----------------------------------------------------------------

def run_install(client, manifest, origin_of: Callable[[str, str], str]) -> list[dict]:
    """Materialize the inc1 kinds in order, capturing each object's real id (so
    uninstall can reverse it) + its create/noop/failed action. One ledger row
    per materialized object, origin-tagged."""
    from packctl.manifest import load_component_file  # noqa: PLC0415

    records: list[dict] = []

    def do(kind, comp, name, target_id_call):
        before = len(client.actions)
        obj_id = target_id_call()
        acts = client.actions[before:]
        # The object's OWN action is the first one this ensure_* recorded
        # (a later 'verify' row, e.g. role→group binding, is not the object's).
        first = acts[0] if acts else {}
        action = first.get("action") or ("failed" if obj_id is None else "create")
        urn = first.get("urn")
        records.append({
            "id": str(uuid.uuid4()), "kind": kind, "identity": name,
            "target_urn": urn, "target_id": obj_id or _urn_id(urn),
            "origin": origin_of(kind, name), "action": action,
            "detail": first.get("detail", ""),
            "reversible": kind in REVERSIBLE_KINDS and action == "create" and bool(obj_id),
        })

    for kind in INC1_KINDS:
        for comp in manifest.components_of(kind):
            doc = load_component_file(manifest, comp)
            if kind == "dispositions":
                for d in doc:
                    do("dispositions", comp, d["code"],
                       lambda d=d: client.ensure_disposition(
                           comp.identity, d["code"], d["label"], d["category"],
                           d.get("requires_note", False)))
            elif kind == "case_fields":
                for f in doc:
                    do("case_fields", comp, f["name"],
                       lambda f=f: client.ensure_case_field(
                           comp.identity, f["name"], f["data_type"],
                           f.get("purpose", "both"), f.get("field_meta")))
            elif kind == "case_schemas":
                for sc in doc:
                    do("case_schemas", comp, sc["schema_key"],
                       lambda sc=sc: client.ensure_case_schema(
                           comp.identity, sc["schema_key"], sc["name"],
                           sc.get("fields", []), sc.get("description", "")))
            elif kind == "display_labels":
                for lbl in doc:
                    do("display_labels", comp, lbl["key"],
                       lambda lbl=lbl: client.ensure_label(
                           comp.identity, lbl["key"], lbl["value"]))
            elif kind == "guardrails":
                for gd in doc:
                    env = _guardrail_envelope(gd, client.workspace_id)
                    do("guardrails", comp, gd["agent_key"],
                       lambda gd=gd, env=env: client.ensure_guardrail(
                           comp.identity, gd["agent_key"], env))
            elif kind == "agent_configs":
                for ac in doc:
                    do("agent_configs", comp, ac["agent_key"],
                       lambda ac=ac: ac["agent_key"] if client.ensure_agent_config(
                           comp.identity, ac["agent_key"], ac.get("prompt_params", {}),
                           ac.get("enabled", True)) else None)
            elif kind == "eval_sets":
                for es in doc:
                    do("eval_sets", comp, es["dataset_key"],
                       lambda es=es: client.ensure_eval_set(
                           comp.identity, es["dataset_key"], es["agent_key"],
                           es.get("cases", []), es.get("description", "")))
            elif kind == "model_archetypes":
                for a in doc:
                    do("model_archetypes", comp, a["archetype_key"],
                       lambda a=a: client.ensure_archetype(
                           comp.identity, a["archetype_key"], a["name"], a["task_type"],
                           a.get("target"), a.get("description", ""),
                           a.get("expected_metrics"), a.get("governance_notes")))
            elif kind == "ontology":
                for ent in doc:
                    do("ontology", comp, ent["entity_key"],
                       lambda ent=ent: client.ensure_ontology_entity(
                           comp.identity, ent["entity_key"], ent["name"],
                           ent.get("attributes"), ent.get("relationships"),
                           ent.get("description", "")))
            elif kind == "write_adapters":
                for wa in doc:
                    do("write_adapters", comp, wa["name"],
                       lambda wa=wa: client.ensure_write_adapter(
                           comp.identity, wa["name"], wa["connector_type"],
                           wa.get("config", {}), wa.get("direction", "outgoing")))
            elif kind == "connection_templates":
                for ct in doc:
                    do("connection_templates", comp, ct["name"],
                       lambda ct=ct: client.ensure_connection_template(
                           comp.identity, ct["name"], ct["connector_type"],
                           ct.get("config", {}), ct.get("direction", "incoming")))
            elif kind == "roles":
                for role in doc:
                    do("roles", comp, role["name"],
                       lambda role=role: client.ensure_role(
                           comp.identity, role["name"], role["actions"]))
            elif kind == "decision_models":
                for dm in (doc if isinstance(doc, list) else [doc]):
                    do("decision_models", comp, dm["name"],
                       lambda dm=dm: client.ensure_decision_model(
                           dm.get("identity", comp.identity), dm["name"], dm["rules"],
                           dm.get("default_outcome")))

    # inc2 data chain: datasets + semantic/verified DRAFTS + saved queries.
    # Dashboards are held for run_complete (they need the model's published
    # measure projection — a steward must approve the model first).
    data_records, pending_dashboards = run_data_chain(client, manifest, origin_of)
    records.extend(data_records)
    return records, pending_dashboards


def _urn_id(urn: str | None) -> str | None:
    if not urn or "/" not in urn:
        return None
    return urn.rsplit("/", 1)[-1]


def run_uninstall(client, ledger: list[dict]) -> list[dict]:
    """Reverse what the pack created (PKG-FR-025). Kinds with a real Core delete
    verb are deleted; the rest are tombstoned (retained, pack-origin cleared)
    with an honest reason. Returns per-row outcomes."""
    e = client.endpoints
    tok = client.author_token()
    outcomes: list[dict] = []
    for row in ledger:
        if row.get("tombstoned"):
            continue
        kind, tid = row["kind"], row.get("target_id")
        if kind == "roles" and row.get("reversible") and tid:
            # A role can't be deleted while bound to its permission group (409).
            # ensure_role creates a same-named permission group + binds the role,
            # so unbind (+ drop the group) before deleting the role.
            _unbind_role_group(client, e, tok, role_name=row["identity"], role_id=tid)
            r = client._req("DELETE", f"{e.rbac}/api/v1/roles/{tid}", tok)
            ok = r.status_code in (200, 204)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "role + permission group removed" if ok
                                       else f"delete {r.status_code}"})
        elif kind == "saved_queries" and tid:
            r = client._req("DELETE", f"{e.query}/api/v1/queries/{tid}", tok)
            ok = r.status_code in (200, 204)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "deleted" if ok else f"delete {r.status_code}"})
        elif kind == "case_fields" and tid:
            ok = client.delete_case_field(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "case field removed" if ok else "delete failed"})
        elif kind == "case_schemas" and tid:
            ok = client.delete_case_schema(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "case schema removed" if ok else "delete failed"})
        elif kind == "ontology" and tid:
            ok = client.delete_ontology_entity(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "ontology entity removed" if ok else "delete failed"})
        elif kind == "write_adapters" and tid:
            ok = client.delete_write_adapter(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "write adapter (outgoing connection) removed" if ok else "delete failed"})
        elif kind == "connection_templates" and tid:
            ok = client.delete_connection_template(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "source connector (incoming connection) removed" if ok else "delete failed"})
        elif kind == "display_labels" and tid:
            ok = client.delete_label(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "label reverted to base string" if ok else "delete failed"})
        elif kind == "guardrails" and tid:
            ok = client.delete_guardrail(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "agent guardrail cleared" if ok else "clear failed"})
        elif kind == "agent_configs" and tid:
            ok = client.clear_agent_config(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "agent specialization cleared" if ok else "clear failed"})
        elif kind == "pipelines" and tid:
            ok = client.delete_pipeline(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "pipeline archived" if ok else "archive failed"})
        elif kind == "memories" and tid:
            ok = client.delete_memory(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "grounding record deleted" if ok else "delete failed"})
        elif kind == "model_archetypes" and tid:
            ok = client.delete_archetype(tid)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "model archetype deleted" if ok else "delete failed"})
        elif kind == "dashboards" and tid:
            r = client._req("DELETE", f"{e.chart}/api/v1/dashboards/{tid}", tok)
            ok = r.status_code in (200, 204)
            outcomes.append({"ledger_id": row["id"], "deleted": ok,
                             "detail": "deleted" if ok else f"delete {r.status_code}"})
        else:
            outcomes.append({"ledger_id": row["id"], "deleted": False,
                             "detail": f"Core exposes no revert verb for '{kind}'; "
                                       "object retained, pack-origin marker cleared"})
    return outcomes


JSON_H = {"Content-Type": "application/json"}


def _rec(kind: str, identity: str, origin_of, *, action: str, urn=None,
         target_id=None, detail="", reversible=False) -> dict:
    return {
        "id": str(uuid.uuid4()), "kind": kind, "identity": identity,
        "target_urn": urn, "target_id": target_id or _urn_id(urn),
        "origin": origin_of(kind, identity), "action": action, "detail": detail,
        "reversible": reversible,
    }


def _measure_urn(client, name: str) -> str:
    return f"wr:{client.tenant_id}:semantic:measure/{name}"


def _expand_sources(client, sources, saved_query_ids: dict) -> list[dict]:
    out = []
    for i, s in enumerate(sources or []):
        if "measure" in s:
            out.append({"position": i, "source_type": "semantic_measure",
                        "source_urn": _measure_urn(client, s["measure"])})
        elif "saved_query" in s:
            qid = saved_query_ids.get(s["saved_query"])
            out.append({"position": i, "source_type": "saved_query",
                        "source_urn": f"wr:{client.tenant_id}:query:query/{qid}"})
        else:
            out.append({"position": i, **s})
    return out


def _semantic_draft(client, name: str, desc: str, definition: dict):
    """Create + PATCH + SUBMIT a semantic model as a governed DRAFT — never
    approve (four-eyes: a distinct human steward publishes it). Returns
    (model_id, published, detail)."""
    import time  # noqa: PLC0415

    e = client.endpoints
    author = client.author_token()
    r = client._req("GET", f"{e.semantic}/api/v1/models?filter[workspace_id]={client.workspace_id}", author)
    model = None
    if r.status_code == 200:
        for m in r.json().get("data", []):
            if m.get("name") == name:
                model = m
                break
    if model and model.get("published_version_id"):
        return model["id"], True, f"{name!r} already published"
    if not model:
        cr = client._req("POST", f"{e.semantic}/api/v1/models", author, headers=JSON_H,
                         json={"workspace_id": client.workspace_id, "name": name,
                               "description": desc, "definition": definition})
        if cr.status_code != 201:
            return None, False, f"create {cr.status_code}: {cr.text[:200]}"
        model = cr.json()["data"]
    mid = model["id"]
    client._req("PATCH", f"{e.semantic}/api/v1/models/{mid}/versions/1", author,
                headers=JSON_H, json={"definition": definition})
    # submit; the dataset projection is eventually consistent with ingestion, so
    # a just-ingested dataset can 422 "not found" for a moment — retry.
    rs = client._req("POST", f"{e.semantic}/api/v1/models/{mid}/versions/1/submit",
                     author, headers=JSON_H, json={})
    for _ in range(6):
        if not (rs.status_code == 422 and "not found" in rs.text):
            break
        time.sleep(2.0)
        rs = client._req("POST", f"{e.semantic}/api/v1/models/{mid}/versions/1/submit",
                         author, headers=JSON_H, json={})
    if rs.status_code != 200:
        return mid, False, f"submit {rs.status_code}: {rs.text[:200]}"
    return mid, False, "submitted — awaiting a steward's four-eyes approval"


def _semantic_published(client, name: str):
    """(model_id, published?) for a pack semantic model by name."""
    e = client.endpoints
    r = client._req("GET", f"{e.semantic}/api/v1/models?filter[workspace_id]={client.workspace_id}",
                    client.author_token())
    if r.status_code == 200:
        for m in r.json().get("data", []):
            if m.get("name") == name:
                return m["id"], bool(m.get("published_version_id"))
    return None, False


def run_data_chain(client, manifest, origin_of):
    """inc2 phase 1: ingest datasets, author the semantic model + verified
    queries as governed DRAFTS (submitted, NOT approved), create saved queries.
    Returns (records, pending_dashboards: bool)."""
    from pathlib import Path  # noqa: PLC0415

    from packctl.manifest import load_component_file  # noqa: PLC0415

    records: list[dict] = []
    dataset_urns: dict[str, str] = {}

    # datasets — ingested as the installing user (no four-eyes).
    for comp in manifest.components_of("datasets"):
        doc = load_component_file(manifest, comp)
        for ds in (doc if isinstance(doc, list) else [doc]):
            path = Path(manifest.pack_dir) / ds["file"]
            urn = client.ensure_dataset(ds["identity"], ds["name"],
                                        path.read_bytes(), ds.get("format", "csv"))
            act = client.actions[-1] if client.actions else {}
            if urn:
                dataset_urns[ds["identity"]] = urn
            records.append(_rec("datasets", ds["name"], origin_of,
                                action=act.get("action", "failed" if not urn else "create"),
                                urn=urn, detail=act.get("detail", "")))

    # semantic models — authored as governed DRAFTS (submitted, not approved).
    for comp in manifest.components_of("semantic_models"):
        doc = load_component_file(manifest, comp)
        definition = dict(doc["definition"])
        for entity in definition.get("entities", []):
            ref = entity.pop("dataset", None)
            if ref and dataset_urns.get(ref):
                entity["dataset_urn"] = dataset_urns[ref]
        mid, published, detail = _semantic_draft(client, doc["name"], doc.get("description", ""), definition)
        action = "failed" if mid is None else ("noop" if published else "submitted")
        records.append(_rec("semantic_models", doc["name"], origin_of,
                            action=action, target_id=mid,
                            urn=(f"wr:{client.tenant_id}:semantic:model/{mid}" if mid else None),
                            detail=detail))

    # verified queries — create + submit as drafts (no approve).
    for comp in manifest.components_of("verified_queries"):
        doc = load_component_file(manifest, comp)
        for i, vq in enumerate(doc):
            e = client.endpoints
            author = client.author_token()
            cr = client._req("POST", f"{e.semantic}/api/v1/verified-queries", author, headers=JSON_H,
                             json={"workspace_id": client.workspace_id, "nl_text": vq["nl_text"],
                                   "sql_text": vq["sql_text"], "model": vq.get("model"),
                                   "tags": vq.get("tags", [])})
            if cr.status_code == 201:
                vid = cr.json()["data"]["id"]
                client._req("POST", f"{e.semantic}/api/v1/verified-queries/{vid}/submit", author, headers=JSON_H, json={})
                records.append(_rec("verified_queries", f"{comp.identity}_{i}", origin_of,
                                    action="submitted", target_id=vid, detail="submitted — awaiting approval"))
            else:
                # 409 = already present (idempotent) → noop; else honest failure.
                records.append(_rec("verified_queries", f"{comp.identity}_{i}", origin_of,
                                    action="noop" if cr.status_code == 409 else "failed",
                                    detail=cr.text[:120]))

    # saved queries — created against the ingested datasets (no four-eyes).
    for comp in manifest.components_of("saved_queries"):
        doc = load_component_file(manifest, comp)
        for q in (doc if isinstance(doc, list) else [doc]):
            qid = client.ensure_saved_query(q["identity"], q["name"], q["sql"],
                                            q.get("description", ""), q.get("tags", []))
            act = client.actions[-1] if client.actions else {}
            records.append(_rec("saved_queries", q["name"], origin_of,
                                action=act.get("action", "failed" if not qid else "create"),
                                target_id=qid, urn=act.get("urn"),
                                reversible=(act.get("action") == "create" and bool(qid)),
                                detail=act.get("detail", "")))

    # seeded case queue — one OPEN case per row, so an analyst sees a real
    # worklist on day one. Needs the pack's dataset ingested above (the cases
    # reference its URN); create_cases is idempotent per row_pk. NOT reversible:
    # cases are operational records an analyst works (dispositions get applied),
    # and case-service has no delete verb — uninstall tombstones them honestly.
    for comp in manifest.components_of("cases"):
        doc = load_component_file(manifest, comp)
        ref = doc.get("dataset")
        urn = dataset_urns.get(ref)
        if not urn:
            records.append(_rec("cases", comp.identity, origin_of, action="failed",
                                detail=f"unknown dataset ref {ref!r} (dataset not ingested)"))
            continue
        # create_cases dedups server-side per (dataset_urn, row_pk), so a
        # re-install returns the existing case ids rather than duplicating.
        ids = client.create_cases(comp.identity, urn, doc["rows"], doc.get("due_days", 7))
        records.append(_rec("cases", comp.identity, origin_of,
                            action="create" if ids else "noop", urn=urn,
                            detail=f"{len(ids)} seed cases materialized (reindexed)" if ids
                            else "no seed cases materialized"))

    # pipeline seeds — algorithm-template pipelines trained on the pack's dataset
    # (invoice-anomaly detector, exception-outcome scorer). Each references a
    # dataset URN → needs the dataset ingested above. Idempotent by name;
    # reversible (DELETE archives the template).
    for comp in manifest.components_of("pipelines"):
        doc = load_component_file(manifest, comp)
        for p in (doc if isinstance(doc, list) else [doc]):
            ref = p.get("dataset")
            urn = dataset_urns.get(ref)
            if not urn:
                records.append(_rec("pipelines", p["name"], origin_of, action="failed",
                                    detail=f"unknown dataset ref {ref!r} (dataset not ingested)"))
                continue
            pid = client.ensure_pipeline(comp.identity, p["algorithm"], p["name"], urn,
                                         p.get("mode", "train"))
            act = client.actions[-1] if client.actions else {}
            records.append(_rec("pipelines", p["name"], origin_of,
                                action=act.get("action", "failed" if not pid else "create"),
                                target_id=pid,
                                reversible=(act.get("action") == "create" and bool(pid)),
                                detail=act.get("detail", "")))

    # tenant-scope RAG grounding — the pack's curated domain knowledge the agents
    # retrieve. Authored AS the installing user via the governed
    # memory.corpus.admin path (NOT agent impersonation). Idempotent by source
    # tag; each record is reversible (deleted by id on uninstall).
    for comp in manifest.components_of("memories"):
        doc = load_component_file(manifest, comp)
        tag = f"pack:{manifest.name}"
        ids = client.ensure_memories(comp.identity, doc, tag)
        if ids:
            for mid in ids:
                records.append(_rec("memories", comp.identity, origin_of, action="create",
                                    target_id=mid, reversible=True,
                                    detail="tenant grounding record"))
        else:
            act = client.actions[-1] if client.actions else {}
            records.append(_rec("memories", comp.identity, origin_of,
                                action=act.get("action", "noop"),
                                detail=act.get("detail", "grounding already present")))

    pending_dashboards = len(manifest.components_of("dashboards")) > 0
    return records, pending_dashboards


def run_complete(client, manifest, origin_of):
    """inc2 phase 2: once a steward has published the pack's semantic model(s),
    materialize the dashboards (their measure projection now resolves). Returns
    (records, ok, detail). ok=False (with detail) if a model is still awaiting
    approval — nothing is materialized."""
    # Every semantic model the pack ships must be published first.
    for comp in manifest.components_of("semantic_models"):
        from packctl.manifest import load_component_file  # noqa: PLC0415
        doc = load_component_file(manifest, comp)
        _mid, pub = _semantic_published(client, doc["name"])
        if not pub:
            return [], False, f"semantic model {doc['name']!r} is not published yet — a steward must approve it first"

    from packctl.manifest import load_component_file  # noqa: PLC0415

    records: list[dict] = []
    # re-derive saved-query ids (dashboards may source them) by name.
    saved_query_ids: dict[str, str] = {}
    for comp in manifest.components_of("saved_queries"):
        for q in (load_component_file(manifest, comp) or []):
            e = client.endpoints
            r = client._req("GET", f"{e.query}/api/v1/queries?workspace_id={client.workspace_id}", client.author_token())
            if r.status_code == 200:
                for row in r.json().get("data", []):
                    if row.get("name") == q["name"]:
                        saved_query_ids[q["identity"]] = row["id"]

    for comp in manifest.components_of("dashboards"):
        spec = dict(load_component_file(manifest, comp))
        for chart in spec.get("charts", []):
            chart["sources"] = _expand_sources(client, chart.get("sources", []), saved_query_ids)
        res = client.ensure_dashboard(comp.identity, spec)
        act = client.actions[-1] if client.actions else {}
        did = res.get("id") if isinstance(res, dict) else None
        records.append(_rec("dashboards", spec.get("name", comp.identity), origin_of,
                            action=("create" if did else "failed"),
                            target_id=did, urn=(f"wr:{client.tenant_id}:chart:dashboard/{did}" if did else None),
                            reversible=bool(did),
                            detail=f"{res.get('warmed', 0)}/{res.get('total', 0)} charts resolve data" if isinstance(res, dict) else ""))
    return records, True, "dashboards materialized"


# ---- version lifecycle: upgrade + rollback (PKG-FR-003/026) -----------------

def snapshot_bundle(manifest) -> dict:
    """Capture the pack bundle (pack.yaml + every referenced component file) as
    text, keyed by path relative to the pack dir. Stored on the install row so a
    later ROLLBACK can re-apply THIS exact version verbatim even after the
    on-disk pack has moved on — the snapshot is the durable version record while
    the signed OCI registry (PKG-FR-005) is deferred."""
    from pathlib import Path  # noqa: PLC0415

    root = Path(manifest.pack_dir)
    files: dict[str, str] = {"pack.yaml": (root / "pack.yaml").read_text()}
    for comp in manifest.components:
        if comp.file not in files:
            files[comp.file] = (root / comp.file).read_text()
    return {"name": manifest.name, "version": manifest.version, "files": files}


def rehydrate_bundle(snapshot: dict, dest_dir: str):
    """Write a stored bundle back to a temp pack dir and load it as a validated
    Manifest — the authoritative component set of that version, independent of
    whatever is on disk now. Used by rollback to re-apply a prior version."""
    from pathlib import Path  # noqa: PLC0415

    from packctl.manifest import load_manifest  # noqa: PLC0415

    root = Path(dest_dir)
    for rel, text in (snapshot.get("files") or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return load_manifest(root)


def _target_object_names(manifest) -> list[tuple[str, str]]:
    """Every (kind, object-name) a manifest would materialize — the SAME names
    the ledger records per object, so a diff over the ledger lines up 1:1."""
    out: list[tuple[str, str]] = []
    for comp in manifest.components:
        for name in _component_names(manifest, comp):
            out.append((comp.kind, name))
    return out


def diff_plan(prior_ledger: list[dict], target_manifest) -> dict:
    """Version diff (PKG-FR-003): compare the objects a target version would
    materialize against a prior install's LIVE ledger (non-tombstoned rows).
    Returns added / removed / retained as {kind, name} lists plus the prior
    ledger ROWS to reverse for the removed set. Pure — no Core calls."""
    target_set = set(_target_object_names(target_manifest))
    live = [(r["kind"], r["identity"], r) for r in prior_ledger if not r.get("tombstoned")]
    live_keys = {(k, n) for k, n, _ in live}

    added = [{"kind": k, "name": n} for (k, n) in sorted(target_set) if (k, n) not in live_keys]
    retained = [{"kind": k, "name": n} for (k, n) in sorted(target_set) if (k, n) in live_keys]
    removed_rows = [r for (k, n, r) in live if (k, n) not in target_set]
    removed = [{"kind": r["kind"], "name": r["identity"]} for r in removed_rows]
    return {"added": added, "removed": removed, "retained": retained,
            "removed_rows": removed_rows}


def run_upgrade(client, target_manifest, prior_ledger, origin_of):
    """Apply a target version over a prior install — the shared core of both
    upgrade and rollback. Materializes the target (idempotent: added objects
    create, retained objects no-op) and REVERSES the components the target no
    longer contains. Pack-reversibility from the prior install is carried onto
    retained rows so the new install stays fully reversible on a later uninstall.
    Returns (new_ledger, pending_dashboards, removed_outcomes, diff)."""
    diff = diff_plan(prior_ledger, target_manifest)
    new_ledger, pending = run_install(client, target_manifest, origin_of)

    # A retained object was created by the pack in the PRIOR version, so although
    # this run sees it as a no-op (it already exists) it remains pack-owned and
    # reversible — we still hold its id via the idempotent ensure_*.
    prior_reversible = {(r["kind"], r["identity"]) for r in prior_ledger if r.get("reversible")}
    for row in new_ledger:
        if (row["kind"], row["identity"]) in prior_reversible \
                and row.get("target_id") and row["kind"] in REVERSIBLE_KINDS:
            row["reversible"] = True

    removed_outcomes = run_uninstall(client, diff["removed_rows"])
    return new_ledger, pending, removed_outcomes, diff


# ---- drift detection (PKG-FR-031: materialized object vs pack intent) -------
#
# After install, a tenant admin can edit or delete a pack-materialized object out
# of band. Drift detection compares each LIVE object to what the pack SHIPPED
# (the intended spec, parsed from the install's bundle snapshot) and reports it as
# in_sync / modified / missing. Two tiers of reader:
#   * CONTENT readers (case_fields, dispositions) fetch the live object and diff
#     its canonical fields against the shipped spec -> in_sync | modified | missing.
#   * PRESENCE (any kind _existing_names can list) confirms the object still
#     exists -> in_sync (presence only) | missing.
# Kinds with neither are reported `unverified` (honest — no reader yet).

def _dispo_intended(entry: dict) -> dict:
    return {"label": entry.get("label"), "category": entry.get("category"),
            "requires_note": bool(entry.get("requires_note", False))}


def _dispo_live(client) -> dict[str, dict]:
    e, tok = client.endpoints, client.author_token()
    r = client._req("GET", f"{e.case}/api/v1/dispositions?workspace_id={client.workspace_id}", tok)
    out: dict[str, dict] = {}
    if r.status_code == 200:
        for d in r.json().get("data", []):
            out[str(d.get("code"))] = {"label": d.get("label"), "category": d.get("category"),
                                       "requires_note": bool(d.get("requires_note", False))}
    return out


def _field_intended(entry: dict) -> dict:
    return {"data_type": entry.get("data_type"), "purpose": entry.get("purpose", "both"),
            "field_meta": entry.get("field_meta") or {}}


def _field_live(client) -> dict[str, dict]:
    e, tok = client.endpoints, client.author_token()
    r = client._req("GET", f"{e.case}/api/v1/case-fields", tok)
    out: dict[str, dict] = {}
    if r.status_code == 200:
        for f in r.json().get("data", []):
            out[str(f.get("name"))] = {"data_type": f.get("data_type"),
                                       "purpose": f.get("purpose"),
                                       "field_meta": f.get("field_meta") or {}}
    return out


# kind -> (fetch live {identity: canon}, shipped-entry -> canon)
DRIFT_CONTENT = {
    "dispositions": (_dispo_live, _dispo_intended),
    "case_fields": (_field_live, _field_intended),
}


def _canon(d: dict) -> str:
    import json  # noqa: PLC0415
    return json.dumps(d, sort_keys=True, default=str)


def _named_entries(kind: str, doc) -> list[tuple[str, dict]]:
    """(object-name, shipped-entry) pairs for a content kind — the entry is the
    pack's intended spec for that object, keyed by the same name the ledger uses."""
    if kind == "dispositions":
        return [(d["code"], d) for d in doc]
    if kind == "case_fields":
        return [(f["name"], f) for f in doc]
    return []


def detect_drift(client, ledger: list[dict], manifest) -> list[dict]:
    """Compare each non-tombstoned ledger object to Core's current state. Returns
    one row per object: {kind, identity, status, contentChecked, detail}. `status`
    is in_sync | modified | missing | unverified. `manifest` (rehydrated from the
    install's bundle snapshot) supplies the intended spec for CONTENT kinds; when
    it is None (a pre-snapshot install) content kinds fall back to presence."""
    intended: dict[tuple[str, str], dict] = {}
    if manifest is not None:
        from packctl.manifest import load_component_file  # noqa: PLC0415
        for comp in manifest.components:
            if comp.kind in DRIFT_CONTENT:
                for name, entry in _named_entries(comp.kind, load_component_file(manifest, comp)):
                    intended[(comp.kind, name)] = entry

    present = _existing_names(client)          # presence per kind (reused)
    content_live: dict[str, dict] = {}         # per content-kind live objects (lazy)
    rows: list[dict] = []
    for r in ledger:
        if r.get("tombstoned"):
            continue
        kind, name = r["kind"], r["identity"]
        spec = intended.get((kind, name))
        if kind in DRIFT_CONTENT and spec is not None:
            if kind not in content_live:
                content_live[kind] = DRIFT_CONTENT[kind][0](client)
            live = content_live[kind]
            if name not in live:
                rows.append(_drift_row(r, "missing", True, "object deleted in Core"))
                continue
            want = DRIFT_CONTENT[kind][1](spec)
            got = live[name]
            if _canon(want) == _canon(got):
                rows.append(_drift_row(r, "in_sync", True, ""))
            else:
                changed = sorted(k for k in want if _canon({k: want.get(k)}) != _canon({k: got.get(k)}))
                rows.append(_drift_row(r, "modified", True,
                                       f"changed: {', '.join(changed)}"))
        elif kind in present:
            if name in present[kind]:
                rows.append(_drift_row(r, "in_sync", False, "present (content not compared)"))
            else:
                rows.append(_drift_row(r, "missing", True, "object deleted in Core"))
        else:
            rows.append(_drift_row(r, "unverified", False, "no drift reader for this kind yet"))
    return rows


def _drift_row(r: dict, status: str, content_checked: bool, detail: str) -> dict:
    return {"kind": r["kind"], "identity": r["identity"], "target_id": r.get("target_id"),
            "origin": r.get("origin"), "status": status,
            "contentChecked": content_checked, "detail": detail}


def _unbind_role_group(client, e, tok, *, role_name: str, role_id: str) -> None:
    """Unbind a pack role from its same-named permission group and drop the
    group, so the role becomes deletable (rbac 409s on a still-bound role)."""
    g = client._req("GET", f"{e.rbac}/api/v1/groups?filter[group_type]=permission&limit=300", tok)
    if g.status_code != 200:
        return
    grp = next((x for x in g.json().get("data", []) if x.get("name") == role_name), None)
    if not grp:
        return
    gid = grp["id"]
    client._req("DELETE", f"{e.rbac}/api/v1/groups/{gid}/roles/{role_id}", tok)
    client._req("DELETE", f"{e.rbac}/api/v1/groups/{gid}", tok)


def origin_tag(pack: str, version: str) -> Callable[[str, str], str]:
    def _of(kind: str, identity: str) -> str:
        return f"pack:{pack}@{version}:{kind}/{identity}"

    return _of


def to_jsonable(v: Any) -> Any:  # pragma: no cover
    return v
