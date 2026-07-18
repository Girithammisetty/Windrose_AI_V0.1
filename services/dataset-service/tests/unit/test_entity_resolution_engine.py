"""Entity-resolution engine (BRD 56 inc1). Proves the two-stage resolver:
deterministic keys merge exact matches; probabilistic scoring auto-merges strong
similarity, PROPOSES borderline matches for four-eyes review, and keeps weak ones
separate; clusters are stable/reproducible with lineage; nothing merges on a
partial key or across a blocking-field disagreement."""

from __future__ import annotations

from app.domain.entity_resolution import (
    ResolutionConfig,
    ScoringField,
    resolve,
    string_similarity,
)

# person records across systems: same national_id, near-name variants, and a
# genuine different person who only shares a first name.
_ROWS = [
    {"pk": "r1", "name": "Viktor Petrov", "dob": "1980-05-01", "national_id": "AB123"},
    {"pk": "r2", "name": "V. A. Petrov", "dob": "1980-05-01", "national_id": "AB123"},  # det key
    {"pk": "r3", "name": "Victor Petrov", "dob": "1980-05-01", "national_id": None},  # prob
    {"pk": "r4", "name": "Viktor Petroff", "dob": "1975-11-20", "national_id": None},  # blocked
    {"pk": "r5", "name": "Maria Lopez", "dob": "1990-01-01", "national_id": "ZZ999"},   # singleton
]

_CFG = ResolutionConfig(
    entity_type="person",
    deterministic_keys=[["national_id"]],
    scoring_fields=[ScoringField("name", 1.0)],
    blocking_fields=["dob"],
    auto_merge_threshold=0.80,
    review_threshold=0.55,
)


def _cluster_of(result, pk):
    return next(c for c in result.clusters if pk in c.member_pks)


def test_deterministic_key_merges_exact_matches():
    result = resolve(_ROWS, _CFG, pk_column="pk")
    c = _cluster_of(result, "r1")
    assert "r2" in c.member_pks                     # shared national_id
    assert c.method == "deterministic"
    assert c.confidence == 1.0


def test_probabilistic_auto_merges_strong_name_match_same_dob():
    result = resolve(_ROWS, _CFG, pk_column="pk")
    # r3 "Victor Petrov" (same dob as r1/r2) scores high vs r1 -> merged in.
    c = _cluster_of(result, "r1")
    assert "r3" in c.member_pks


def test_blocking_field_disagreement_prevents_merge():
    result = resolve(_ROWS, _CFG, pk_column="pk")
    # r4 has a DIFFERENT dob -> never merged with the Petrov cluster despite the
    # similar name.
    c = _cluster_of(result, "r1")
    assert "r4" not in c.member_pks
    assert _cluster_of(result, "r4").member_pks == ["r4"]


def test_singleton_stays_separate():
    result = resolve(_ROWS, _CFG, pk_column="pk")
    c = _cluster_of(result, "r5")
    assert c.member_pks == ["r5"]
    assert c.method == "singleton" and c.confidence == 1.0


def test_partial_deterministic_key_never_merges():
    # Two records with a MISSING national_id must not merge on the empty key.
    rows = [{"pk": "a", "national_id": None, "name": "X"},
            {"pk": "b", "national_id": None, "name": "Y"}]
    cfg = ResolutionConfig(entity_type="person", deterministic_keys=[["national_id"]])
    result = resolve(rows, cfg, pk_column="pk")
    assert {c.resolved_entity_id for c in result.clusters} == {"ent:person:a", "ent:person:b"}


def test_borderline_becomes_review_candidate_not_merged():
    # A pair between review and auto thresholds is PROPOSED, never auto-merged.
    rows = [{"pk": "a", "name": "Jon Smith", "dob": "2000-01-01"},
            {"pk": "b", "name": "Jonathan Smyth", "dob": "2000-01-01"}]
    cfg = ResolutionConfig(entity_type="person", scoring_fields=[ScoringField("name", 1.0)],
                           blocking_fields=["dob"], auto_merge_threshold=0.95, review_threshold=0.4)
    result = resolve(rows, cfg, pk_column="pk")
    assert len(result.clusters) == 2                          # not merged
    assert len(result.merge_candidates) == 1                  # proposed for review
    cand = result.merge_candidates[0]
    assert {cand.left_pk, cand.right_pk} == {"a", "b"}
    assert 0.4 <= cand.score < 0.95


def test_clusters_are_stable_and_reproducible():
    r1 = resolve(_ROWS, _CFG, pk_column="pk")
    r2 = resolve(list(reversed(_ROWS)), _CFG, pk_column="pk")
    ids1 = sorted(c.resolved_entity_id for c in r1.clusters)
    ids2 = sorted(c.resolved_entity_id for c in r2.clusters)
    assert ids1 == ids2                                       # order-independent (audit)


def test_cluster_carries_lineage_evidence():
    result = resolve(_ROWS, _CFG, pk_column="pk")
    c = _cluster_of(result, "r1")
    # The deterministic merge records its key + values as evidence.
    assert any(e.get("key") == ["national_id"] for e in c.evidence)


def test_string_similarity_bounds():
    assert string_similarity("Petrov", "Petrov") == 1.0
    assert string_similarity("Petrov", "Petroff") > 0.6
    assert string_similarity("Petrov", "Lopez") < 0.3
    assert string_similarity(None, None) == 1.0
    assert string_similarity("x", None) == 0.0
