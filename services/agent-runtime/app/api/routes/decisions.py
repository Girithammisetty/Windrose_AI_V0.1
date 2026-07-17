"""Decision models (BRD 54) — governed decision tables.

Tenant-authored, versioned condition->outcome rules over real columns that
EXECUTE to the same governed four-eyes proposal an agent produces. Authoring is
validated (DM-FR-040); evaluation is deterministic + explainable (DM-FR-010) and
either a dry-run (no side effect, DM-FR-030) or a governed proposal via the
shared ProposalService (DM-FR-020) — inheriting four-eyes, the caller-gate, the
guardrail tool-allowlist, and audit.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request

from app.api.auth import principal_of
from app.domain.decisions import (
    Condition,
    DecisionModel,
    DecisionModelInvalid,
    Outcome,
    Rule,
    evaluate,
    rules_from_json,
    rules_to_json,
    validate_model,
)
from app.domain.entities import Run, new_uuid, now
from app.domain.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.urn import case_urn
from app.graphs.base import WriteIntent
from app.graphs.triage import (
    TRIAGE_TOOL_ID,
    TRIAGE_TOOL_VERSION,
    _resolve_disposition_id,
)

router = APIRouter(prefix="/api/v1")


def _model_view(m: DecisionModel) -> dict:
    return {"id": m.model_id, "name": m.name, "version": m.version, "status": m.status,
            "workspace_id": m.workspace_id, "dataset_urn": m.dataset_urn,
            "rules": rules_to_json(m.rules),
            "default_outcome": (None if m.default_outcome is None else
                                {"disposition_code": m.default_outcome.disposition_code,
                                 "severity": m.default_outcome.severity})}


async def _require(request: Request, principal, action: str) -> None:
    c = request.app.state.container
    # case.disposition.* are WORKSPACE-SCOPED caps — the projection is keyed by
    # workspace, so the check must carry the caller's workspace or it never
    # matches (a known gotcha).
    if not await c.authz.allow(subject=principal.actor, action=action,
                               tenant=principal.tenant_id,
                               workspace_id=getattr(principal, "workspace_id", None)):
        raise PermissionDenied(f"{action} capability required")


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:] if h.lower().startswith("bearer ") else h


async def _catalog_codes(c, tenant_id: str, token: str) -> set[str] | None:
    if c.case_reader is None or not hasattr(c.case_reader, "list_dispositions"):
        return None
    rows = await c.case_reader.list_dispositions(tenant_id=tenant_id, auth_token=token)
    return {str(d.get("code")) for d in rows if d.get("code")} or None


@router.post("/decision-models", status_code=201)
async def create_decision_model(request: Request, body: dict = Body(...)):
    """Author a decision table (DM-FR-001/040/050). Validated against the
    workspace disposition catalog; published tenant-scoped."""
    principal = await principal_of(request)
    # inc1: authoring reuses the disposition-management capability pack manager
    # roles already grant; a dedicated decision.model.* action is inc2.
    await _require(request, principal, "case.disposition.create")
    c = request.app.state.container

    name = str(body.get("name") or "").strip()
    rules = rules_from_json(body.get("rules"))
    default_outcome = None
    if body.get("default_outcome"):
        d = body["default_outcome"]
        default_outcome = Outcome(str(d.get("disposition_code", "")),
                                  str(d.get("severity", "")))
    codes = await _catalog_codes(c, principal.tenant_id, _bearer(request))
    try:
        validate_model(name, rules, default_outcome,
                       valid_codes=codes, schema_columns=None)
    except DecisionModelInvalid as exc:
        raise ValidationFailed(str(exc)) from exc

    model = DecisionModel(
        model_id=new_uuid(), tenant_id=principal.tenant_id, name=name,
        version=int(body.get("version", 1)),
        workspace_id=body.get("workspace_id") or getattr(principal, "workspace_id", None),
        dataset_urn=body.get("dataset_urn"), rules=rules,
        default_outcome=default_outcome, status="published",
        created_by=principal.sub)
    await c.store.create_decision_model(model)
    return {"data": _model_view(model)}


@router.get("/decision-models")
async def list_decision_models(request: Request):
    principal = await principal_of(request)
    await _require(request, principal, "case.disposition.read")
    c = request.app.state.container
    models = await c.store.list_decision_models(principal.tenant_id)
    return {"data": [_model_view(m) for m in models]}


@router.get("/decision-models/{model_id}")
async def get_decision_model(request: Request, model_id: str):
    principal = await principal_of(request)
    await _require(request, principal, "case.disposition.read")
    c = request.app.state.container
    m = await c.store.get_decision_model(principal.tenant_id, model_id)
    if m is None:
        raise NotFound("decision model not found")
    return {"data": _model_view(m)}


def _fields_of(case: dict, extra: dict | None) -> dict:
    """The evaluable field map for a case: projected decision fields + scalar
    top-level fields, then any caller-supplied `fields` (e.g. model-score
    columns the UI resolved) layered on top."""
    base = {**(case.get("display_projection") or {}),
            **{k: v for k, v in case.items() if not isinstance(v, (dict, list))}}
    if extra:
        base.update(extra)
    return base


async def _propose_for_case(c, principal, m, case: dict, ev,
                            dispositions: list) -> tuple[str, str, bool]:
    """Build the SAME governed proposal single-evaluate does, for one case.
    Returns (proposal_id, status, executed)."""
    case_id = case.get("id") or case.get("case_id")
    disposition_id = _resolve_disposition_id(ev.outcome.disposition_code, dispositions, {})
    workspace_id = case.get("workspace_id") or getattr(principal, "workspace_id", None)
    intent = WriteIntent(
        tool_id=TRIAGE_TOOL_ID, tool_version=TRIAGE_TOOL_VERSION,
        tier="write-proposal", side_effects="reversible",
        args={"case_id": case_id, "severity": ev.outcome.severity,
              "disposition_id": disposition_id,
              "resolution_note": f"Decision model '{m.name}' v{m.version}: {ev.explanation}"},
        rationale=f"Decision table '{m.name}' v{m.version} — {ev.explanation}",
        affected_urns=[case_urn(principal.tenant_id, case_id)],
        workspace_id=workspace_id, required_action="case.case.update",
        predicted_effect={
            "summary": (f"Case {case_id} → severity {ev.outcome.severity}, disposition "
                        f"{ev.outcome.disposition_code} (decision model {m.name})."),
            "reversibility": "reversible", "blast_radius": 1})
    # Synthetic run so the proposal carries provenance = the decision model.
    run = Run(run_id=new_uuid(), tenant_id=principal.tenant_id, session_id=new_uuid(),
              agent_key=f"decision-model:{m.model_id}", agent_version=m.version,
              temporal_workflow_id=None, status="running",
              principal_type="user_obo", obo_sub=principal.sub)
    await c.store.create_run(run)
    prop, executed = await c.proposal_service.create_from_intent(
        run=run, intent=intent, obo_user=principal.sub, auto_execute_policy={})
    return prop.proposal_id, prop.status, executed


@router.post("/decision-models/{model_id}/evaluate")
async def evaluate_decision_model(request: Request, model_id: str,
                                  dry_run: bool = Query(default=False),
                                  body: dict = Body(default={})):
    """Evaluate the model against a case. dry_run=true → outcome + fired rule,
    NO proposal (DM-FR-030). Otherwise → a governed four-eyes proposal via
    ProposalService (DM-FR-020): four-eyes, caller-gate, guardrail, audit."""
    principal = await principal_of(request)
    c = request.app.state.container
    m = await c.store.get_decision_model(principal.tenant_id, model_id)
    if m is None:
        raise NotFound("decision model not found")

    case_id = body.get("case_id")
    if not case_id:
        raise ValidationFailed("case_id is required")
    token = _bearer(request)
    case = await c.case_reader.get_case(
        tenant_id=principal.tenant_id, case_id=case_id, auth_token=token)
    ev = evaluate(m, _fields_of(case, body.get("fields")))

    result = {"matched": ev.matched, "rule_index": ev.rule_index,
              "explanation": ev.explanation,
              "outcome": (None if ev.outcome is None else
                          {"disposition_code": ev.outcome.disposition_code,
                           "severity": ev.outcome.severity})}
    if dry_run or not ev.matched:
        return {"data": {**result, "proposal_id": None, "dry_run": bool(dry_run)}}

    dispositions = await c.case_reader.list_dispositions(
        tenant_id=principal.tenant_id, auth_token=token) \
        if hasattr(c.case_reader, "list_dispositions") else []
    pid, status, executed = await _propose_for_case(c, principal, m, case, ev, dispositions)
    return {"data": {**result, "proposal_id": pid,
                     "proposal_status": status, "executed": executed,
                     "dry_run": False}}


@router.post("/decision-models/{model_id}/batch-evaluate")
async def batch_evaluate_decision_model(request: Request, model_id: str,
                                        propose: bool = Query(default=False),
                                        body: dict = Body(default={})):
    """Run the model across a WORKLIST (DM-FR-060). Body: either an explicit
    `case_ids: [...]` or `{workspace_id?, limit?}` to pull open cases. Default is
    a dry-run PREVIEW (per-case outcome + coverage summary, no side effects);
    with `?propose=true` each matched case gets its own governed four-eyes
    proposal — one decision, one proposal, no batch bypass of approval."""
    principal = await principal_of(request)
    c = request.app.state.container
    m = await c.store.get_decision_model(principal.tenant_id, model_id)
    if m is None:
        raise NotFound("decision model not found")
    token = _bearer(request)

    case_ids = body.get("case_ids")
    if case_ids and not isinstance(case_ids, list):
        raise ValidationFailed("case_ids must be a list")
    if not case_ids:
        ws = body.get("workspace_id") or getattr(principal, "workspace_id", None)
        limit = min(int(body.get("limit", 100)), 500)
        cases = await c.case_reader.list_cases(
            tenant_id=principal.tenant_id, workspace_id=ws, limit=limit,
            auth_token=token) if hasattr(c.case_reader, "list_cases") else []
    else:
        cases = []
        for cid in case_ids[:500]:
            try:
                cases.append(await c.case_reader.get_case(
                    tenant_id=principal.tenant_id, case_id=cid, auth_token=token))
            except Exception:  # noqa: BLE001 — a bad id shouldn't sink the batch
                continue

    dispositions = await c.case_reader.list_dispositions(
        tenant_id=principal.tenant_id, auth_token=token) \
        if propose and hasattr(c.case_reader, "list_dispositions") else []

    rows: list[dict] = []
    by_outcome: dict[str, int] = {}
    matched = proposed = 0
    for case in cases:
        cid = case.get("id") or case.get("case_id")
        ev = evaluate(m, _fields_of(case, None))
        row = {"case_id": cid, "matched": ev.matched, "rule_index": ev.rule_index,
               "explanation": ev.explanation,
               "outcome": (None if ev.outcome is None else
                           {"disposition_code": ev.outcome.disposition_code,
                            "severity": ev.outcome.severity})}
        if ev.matched and ev.outcome is not None:
            matched += 1
            by_outcome[ev.outcome.disposition_code] = \
                by_outcome.get(ev.outcome.disposition_code, 0) + 1
            if propose:
                pid, status, executed = await _propose_for_case(
                    c, principal, m, case, ev, dispositions)
                row.update(proposal_id=pid, proposal_status=status, executed=executed)
                proposed += 1
        rows.append(row)

    return {"data": {"model_id": model_id, "proposed": bool(propose),
                     "summary": {"cases": len(cases), "matched": matched,
                                 "unmatched": len(cases) - matched,
                                 "proposals_created": proposed,
                                 "by_outcome": by_outcome},
                     "results": rows}}
