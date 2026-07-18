"""BRD 56 inc2 — persisted entity resolution: versioned config + run + resolved
clusters + member lineage + the four-eyes merge-candidate queue, over the memory
store. The pure matching engine is covered by test_entity_resolution_engine; this
exercises the SERVICE persistence, read-back, and merge-apply governance."""

from __future__ import annotations

import pytest

from app.domain.errors import NotFound, ValidationFailed
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
    with pytest.raises(NotFound):
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


# ---- inc3: golden-record rollup + governed resolved-entity view -------------

from types import SimpleNamespace  # noqa: E402

from app.domain.entity_resolution import build_golden_records  # noqa: E402


def test_build_golden_records_first_and_numeric_aggs():
    cols, rows = build_golden_records(
        [{"resolved_entity_id": "e1", "member_count": 3, "confidence": 0.9,
          "method": "probabilistic"}],
        {"e1": ["r3", "r1", "r2"]},
        {"r1": {"name": "", "amt": "100"}, "r2": {"name": "Acme", "amt": "50"},
         "r3": {"name": "Acme Corp", "amt": "bad"}},
        [{"column": "name", "agg": "first"}, {"column": "amt", "agg": "sum"},
         {"column": "amt", "agg": "max"}])
    assert cols == ["resolved_entity_id", "member_count", "confidence", "method",
                    "name", "amt", "amt"]
    # first non-empty by SORTED member pk (r1 empty -> r2 "Acme"); sum ignores "bad"
    assert rows[0] == ["e1", "3", "0.9", "probabilistic", "Acme", "150", "100"]


def test_build_golden_records_count_distinct_and_empty():
    cols, rows = build_golden_records(
        [{"resolved_entity_id": "e1", "member_count": 2, "confidence": 1.0,
          "method": "deterministic"}],
        {"e1": ["a", "b"]},
        {"a": {"policy": "P1"}, "b": {"policy": "P1"}},
        [{"column": "policy", "agg": "count_distinct"}, {"column": "missing", "agg": "first"}])
    assert rows[0] == ["e1", "2", "1.0", "deterministic", "1", ""]


class _FakeWriter:
    def __init__(self):
        self.staged = None

    async def stage(self, table, batches, summary):
        cols, rows = [], []
        async for b in batches:
            cols = b.columns
            rows += b.rows
        self.staged = {"table": table, "columns": cols, "rows": rows, "summary": summary}
        return SimpleNamespace(table=table, columns=cols, rows=len(rows),
                               bytes_written=len(rows) * 8, summary=summary,
                               staging_token="t", path="/tmp/x")

    async def commit(self, staged):
        return SimpleNamespace(snapshot_id=987654321, rows_appended=len(self.staged["rows"]),
                               bytes_written=8)


async def _fake_register(self, ctx, dataset_id, payload):
    _fake_register.calls.append({"dataset_id": dataset_id, **payload})
    return SimpleNamespace(version_no=1)


_fake_register.calls = []


async def test_materialize_creates_governed_resolved_dataset(svc, container, monkeypatch):
    out = await _resolve(svc)  # merged cluster r1+r2 (shared national_id) + r3/r4/r5
    writer = _FakeWriter()
    container.dataset_service.deps.iceberg_writer = writer
    _fake_register.calls.clear()
    monkeypatch.setattr("app.domain.services.VersionService.register", _fake_register)

    res = await container.dataset_service.materialize_resolved_entities(
        _ctx(), out["run_id"], name="resolved_person",
        attributes=[{"column": "name", "agg": "first"},
                    {"column": "national_id", "agg": "count_distinct"}],
        workspace_id="ws-claims")

    # one governed row per resolved entity, written to a bronze warehouse table
    assert res["row_count"] == out["resolved_entity_count"]
    assert res["columns"][:4] == ["resolved_entity_id", "member_count", "confidence", "method"]
    assert writer.staged["table"].startswith("bronze.")
    assert len(writer.staged["rows"]) == out["resolved_entity_count"]
    # the immutable version was registered against the committed snapshot
    assert _fake_register.calls and _fake_register.calls[0]["iceberg_snapshot_id"] == 987654321
    assert _fake_register.calls[0]["skip_profiling"] is True
    # the derived dataset is a real governed dataset (RLS by tenant) + audited
    ds, _ver = await container.dataset_service.get(_ctx(), res["resolved_dataset_id"])
    assert ds.name == "resolved_person"
    assert container.memory_state.events_of_type("dataset.entity_resolution.materialized")


async def test_materialize_requires_warehouse_writer(svc, container):
    out = await _resolve(svc)
    container.dataset_service.deps.iceberg_writer = None
    with pytest.raises(ValidationFailed):
        await container.dataset_service.materialize_resolved_entities(
            _ctx(), out["run_id"], workspace_id="ws-claims")
