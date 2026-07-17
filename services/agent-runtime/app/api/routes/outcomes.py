"""Decision outcome monitoring (BRD 55) — realized outcomes on decisions.

Attach a REALIZED outcome to a decision (a proposal produced by an agent,
decision table, or persona copilot), joined on the decision's provenance; read
decision EFFECTIVENESS (decided-vs-realized agreement) sliced by decision type
or producer. Labels annotate, never mutate a closed decision (BR-1); tenant-
scoped (RLS); audited via the store. Correlational effectiveness only (BR-3).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request

from app.api.auth import principal_of
from app.domain.entities import new_uuid
from app.domain.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.outcomes import (
    LABEL_SOURCES,
    OutcomeLabel,
    compute_correct,
    effectiveness,
)

router = APIRouter(prefix="/api/v1")


async def _require(request: Request, principal, action: str) -> None:
    c = request.app.state.container
    if not await c.authz.allow(subject=principal.actor, action=action,
                               tenant=principal.tenant_id,
                               workspace_id=getattr(principal, "workspace_id", None)):
        raise PermissionDenied(f"{action} capability required")


def _label_view(lab: OutcomeLabel) -> dict:
    return {"id": lab.label_id, "decision_ref": lab.decision_ref,
            "decision_type": lab.decision_type, "producer": lab.producer,
            "decided_outcome": lab.decided_outcome,
            "realized_outcome": lab.realized_outcome, "correct": lab.correct,
            "label_source": lab.label_source, "note": lab.note,
            "labeled_by": lab.labeled_by}


def _decided_outcome_of(prop) -> str | None:
    """What the platform DECIDED for this proposal — the disposition-ish outcome
    carried in the proposal args (severity/disposition), else None."""
    a = prop.args or {}
    return (a.get("disposition_code") or a.get("target_stage")
            or a.get("severity") or a.get("disposition_id"))


@router.post("/decisions/{decision_ref}/outcome", status_code=201)
async def mark_outcome(request: Request, decision_ref: str, body: dict = Body(...)):
    """Record the realized outcome of a decision (DM-FR-010 human path). Joins
    the decided outcome + producer from the referenced proposal when it exists,
    so effectiveness needs no extra input. Reuses case.case.update (the pack
    roles that decide also record outcomes); a dedicated decision.outcome.* is
    inc2."""
    principal = await principal_of(request)
    await _require(request, principal, "case.case.update")
    c = request.app.state.container

    realized = str(body.get("realized_outcome") or "").strip()
    if not realized:
        raise ValidationFailed("realized_outcome is required")
    source = str(body.get("label_source") or "human")
    if source not in LABEL_SOURCES:
        raise ValidationFailed(f"label_source must be one of {LABEL_SOURCES}")

    # Join provenance from the proposal (best-effort — decision_ref may also be a
    # case/decision urn labeled directly, in which case the caller supplies type).
    prop = await c.store.get_proposal(principal.tenant_id, decision_ref)
    if prop is not None:
        decision_type = body.get("decision_type") or prop.tool_id
        producer = prop.agent_key
        decided = _decided_outcome_of(prop)
    else:
        decision_type = body.get("decision_type")
        producer = body.get("producer")
        decided = body.get("decided_outcome")
        if not decision_type:
            raise NotFound("no proposal for that decision_ref; pass decision_type")

    lab = OutcomeLabel(
        label_id=new_uuid(), tenant_id=principal.tenant_id, decision_ref=decision_ref,
        decision_type=decision_type, producer=producer, decided_outcome=decided,
        realized_outcome=realized, correct=compute_correct(decided, realized),
        label_source=source, note=body.get("note"), labeled_by=principal.sub)
    await c.store.upsert_outcome_label(lab)
    return {"data": _label_view(lab)}


@router.get("/decisions/{decision_ref}/outcome")
async def get_outcome(request: Request, decision_ref: str):
    principal = await principal_of(request)
    await _require(request, principal, "case.case.read")
    c = request.app.state.container
    lab = await c.store.get_outcome_label(principal.tenant_id, decision_ref)
    if lab is None:
        raise NotFound("no outcome recorded for that decision")
    return {"data": _label_view(lab)}


@router.get("/decision-effectiveness")
async def decision_effectiveness(request: Request,
                                 by: str = Query(default="decision_type"),
                                 decision_type: str | None = Query(default=None)):
    """Decision-effectiveness KPIs: decided-vs-realized agreement, grouped by
    decision_type or producer (OM-FR-020). The monitoring surface that separates
    a DI platform from a static rules engine."""
    principal = await principal_of(request)
    await _require(request, principal, "case.case.read")
    if by not in ("decision_type", "producer"):
        raise ValidationFailed("by must be 'decision_type' or 'producer'")
    c = request.app.state.container
    labels = await c.store.list_outcome_labels(principal.tenant_id,
                                               decision_type=decision_type)
    return {"data": {"by": by, "labeled_decisions": len(labels),
                     "groups": effectiveness(labels, by=by)}}
