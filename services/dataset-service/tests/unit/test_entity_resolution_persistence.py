"""BRD 56 inc2 — persisted entity resolution: versioned config + run + resolved
clusters + member lineage + the four-eyes merge-candidate queue, over the memory
store. The pure matching engine is covered by test_entity_resolution_engine; this
exercises the SERVICE persistence, read-back, and merge-apply governance."""

from __future__ import annotations

import pytest

from app.domain.services import CallCtx
from tests.conftest import TENANT_A, TENANT_B

# A small record set: three records for one real person (shared national_id),
# one clean duplicate by name+dob (probabilistic), and two distinct people.
ROWS = [
    {"pk": "r1", "name": "Viktor Petrov", "national_id": "N-100", "dob": "1980-01-01"},
    {"pk": "r2", "name": "V. Petrov", "national_id": "N-100", "dob": "1980-01-01"},
    {"pk": "r3", "name": "Victor Petrov", "national_id": "", "dob": "1980-01-01"},
    {"pk": "r4", "name": "Jane Smith", "national_id": "N-200", "dob": "1990-05-05"},
    {"pk": "r5", "name": "John Doe", "national_id": "N-300", "dob": "1975-09-09"},
]
COLUMNS = ["pk", "name", "national_id", "dob"]

CONFIG = {
    "entity_type": "person",
    "deterministic_keys": [["national_id"]],
    "scoring_fields": [{"column": "name", "weight": 1.0}],
    "blocking_fields": ["dob"],
    "auto_merge_threshold": 0.85,
    "review_threshold": 0.40,
}


def _ctx(tenant=TENANT_A):
    return CallCtx(tenant_id=tenant, actor={"type": "user", "id": "steward-1"})


@pytest.fixture
def svc(container, monkeypatch):
    s = container.dataset_service

    async def _fake_read_rows(tenant_id, dataset_id, row_limit=20000):
        return COLUMNS, ROWS

    monkeypatch.setattr(s, "read_rows", _fake_read_rows)
    return s


async def _resolve(svc, tenant=TENANT_A, dataset="d-1"):
    return await svc.resolve_entities(
        tenant, dataset, config=CONFIG, pk_column="pk",
        persist=True, ctx=_ctx(tenant), created_by="steward-1")


async def test_persists_run_with_versioned_config(svc, container):
    out = await _resolve(svc)
    assert out["run_id"] and out["config_id"]
    assert out["config_version"] == 1
    # r1/r2 collapse on the shared national_id (deterministic). r3 ("Victor", no
    # national_id) scores below auto-merge vs "Viktor" so it stays its own entity;
    # r4/r5 are distinct -> 4 resolved entities, 1 merged cluster.
    assert out["resolved_entity_count"] == 4
    assert out["merged_cluster_count"] == 1

    runs = await container.dataset_service.list_resolution_runs(TENANT_A, "d-1")
    assert len(runs) == 1 and runs[0]["run_id"] == out["run_id"]

    # a second resolution mints config version 2 (BR-5 re-resolution is versioned)
    out2 = await _resolve(svc)
    assert out2["config_version"] == 2


async def test_run_detail_has_clusters_and_member_lineage(svc, container):
    out = await _resolve(svc)
    detail = await container.dataset_service.get_resolution_run(TENANT_A, out["run_id"])
    merged = [c for c in detail["clusters"] if c["member_count"] > 1]
    assert len(merged) == 1
    cluster = merged[0]
    assert {m["member_pk"] for m in cluster["members"]} == {"r1", "r2"}
    # lineage: every member records the method it joined on (ER-FR-040)
    assert all(m["method"] for m in cluster["members"])


async def test_run_isolated_by_tenant(svc, container):
    out = await _resolve(svc)
    with pytest.raises(Exception):
        await container.dataset_service.get_resolution_run(TENANT_B, out["run_id"])


async def test_merge_candidate_four_eyes_apply(svc, container):
    # Lower auto so the r1/r2/r3-vs nobody stays; craft a near-duplicate pair that
    # lands in the review band (above review, below auto).
    cfg = {**CONFIG, "deterministic_keys": [], "auto_merge_threshold": 0.95,
           "review_threshold": 0.30}
    out = await svc.resolve_entities(
        TENANT_A, "d-2", config=cfg, pk_column="pk", persist=True,
        ctx=_ctx(), created_by="steward-1")
    cands = await container.dataset_service.list_merge_candidates(TENANT_A, out["run_id"])
    assert cands, "expected at least one below-auto review candidate"
    cand = cands[0]
    assert cand["status"] == "pending"

    # steward opens a governed proposal over it (link recorded)
    await container.dataset_service.link_merge_proposal(TENANT_A, cand["id"], "prop-xyz")
    linked = await container.dataset_service.get_merge_candidate(TENANT_A, cand["id"])
    assert linked["proposal_id"] == "prop-xyz"

    # four-eyes approval confirms the merge (link layer only; SoR untouched)
    applied = await container.dataset_service.apply_entity_merge(
        TENANT_A, candidate_id=cand["id"], decided_by="approver-2", approve=True, ctx=_ctx())
    assert applied["status"] == "approved"
    assert applied["decided_by"] == "approver-2"

    # idempotent: a second decide is a no-op returning the settled state
    again = await container.dataset_service.apply_entity_merge(
        TENANT_A, candidate_id=cand["id"], decided_by="approver-3", approve=False, ctx=_ctx())
    assert again["status"] == "approved"


async def test_merge_reject_closes_candidate(svc, container):
    cfg = {**CONFIG, "deterministic_keys": [], "auto_merge_threshold": 0.95,
           "review_threshold": 0.30}
    out = await svc.resolve_entities(
        TENANT_A, "d-3", config=cfg, pk_column="pk", persist=True,
        ctx=_ctx(), created_by="steward-1")
    cand = (await container.dataset_service.list_merge_candidates(TENANT_A, out["run_id"]))[0]
    applied = await container.dataset_service.apply_entity_merge(
        TENANT_A, candidate_id=cand["id"], decided_by="approver-2", approve=False, ctx=_ctx())
    assert applied["status"] == "rejected"


async def test_run_emits_audit_event(svc, container):
    out = await _resolve(svc)
    events = container.memory_state.events_of_type("dataset.entity_resolution.run")
    assert any(e["payload"].get("run_id") == out["run_id"] for e in events)
