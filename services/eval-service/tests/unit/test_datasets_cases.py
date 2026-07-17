"""Golden dataset + case lifecycle & flywheel sourcing (EVL-FR-001..005, BR-3)."""

from __future__ import annotations

import pytest

from app.domain.entities import CallCtx
from app.domain.errors import AnonymizationRequired, FrozenDataset, ValidationFailed

TENANT = "11111111-1111-4111-8111-111111111111"


def ctx(tenant=TENANT):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": "curator-1"})


async def test_dataset_create_and_freeze_guard(container):
    ds = container.dataset_service
    cs = container.case_service
    d = await ds.create(ctx(), {"dataset_key": "analytics/nl2sql", "agent_key": "analytics"})
    assert d.version == 1 and d.status == "draft"
    # freeze with no active cases -> guard 422
    with pytest.raises(ValidationFailed):
        await ds.freeze(ctx(), "analytics/nl2sql", 1)
    await cs.create(
        ctx(),
        {
            "dataset_key": "analytics/nl2sql",
            "agent_key": "analytics",
            "input": {"messages": []},
            "expected": {"kind": "rubric", "value": {}},
            "status": "active",
        },
    )
    frozen = await ds.freeze(ctx(), "analytics/nl2sql", 1)
    assert frozen.status == "frozen" and frozen.frozen_by == "curator-1"


async def test_frozen_dataset_mutation_rejected_copy_on_write(container):  # AC-15
    ds = container.dataset_service
    cs = container.case_service
    await ds.create(ctx(), {"dataset_key": "k/x", "agent_key": "a"})
    c = await cs.create(
        ctx(),
        {
            "dataset_key": "k/x",
            "agent_key": "a",
            "input": {},
            "expected": {"kind": "rubric", "value": {}},
            "status": "active",
        },
    )
    await ds.freeze(ctx(), "k/x", 1)
    with pytest.raises(FrozenDataset):
        await cs.edit(ctx(), c.id, {"weight": 2.0})
    # copy-on-write to next draft
    d2 = await ds.ensure_draft(ctx(), "k/x")
    assert d2.version == 2 and d2.status == "draft" and d2.case_count == 1


async def test_verified_query_auto_active(container):  # AC-4
    cs = container.case_service
    case = await cs.from_verified_query(
        ctx(),
        {
            "agent_key": "analytics",
            "nl": "monthly revenue by region",
            "sql": "SELECT region, SUM(net_revenue) FROM o GROUP BY region",
            "verified_query_urn": "wr:t:semantic:verified_query/vq-88",
        },
    )
    assert case.status == "active"
    assert case.source == "verified_query"
    assert case.expected["value"]["sql"].startswith("SELECT region")
    assert case.input["messages"][0]["content"] == "monthly revenue by region"


async def test_rejection_candidate_requires_attestation(container):  # AC-5
    cs = container.case_service
    case = await cs.from_rejection(
        ctx(),
        {
            "agent_key": "case-triage",
            "proposal_urn": "wr:t:ai:proposal/p1",
            "proposed_action": {"tool": "assign", "args": {"team": "A"}},
            "reason": "wrong assignee — vendor class routes to team B",
            "run_context": {"task": "route this claim"},
        },
    )
    assert case.status == "candidate"
    assert case.expected["value"]["rejection_reason"].startswith("wrong assignee")
    # promotion without attestation -> 422
    with pytest.raises(AnonymizationRequired):
        await cs.promote(ctx(), case.id)
    await cs.attest(ctx(), case.id, "qa-jane")
    promoted = await cs.promote(ctx(), case.id)
    assert promoted.status == "active"


async def test_edit_diff_expected_is_edited_args(container):  # AC-6
    cs = container.case_service
    case = await cs.from_edit_diff(
        ctx(),
        {
            "agent_key": "case-triage",
            "proposal_urn": "wr:t:ai:proposal/p2",
            "tool": "set_severity",
            "edited_args": {"severity": "medium"},
            "diff": {"severity": {"from": "high", "to": "medium"}},
            "run_context": {"task": "triage"},
        },
    )
    assert case.expected["value"]["args"] == {"severity": "medium"}
    assert case.expected["value"]["diff"]["severity"]["to"] == "medium"
    assert case.source == "approval_edit_diff"
