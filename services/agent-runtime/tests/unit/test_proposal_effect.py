"""Server-derived approval effect + risk tiering (P0 approval hardening).

Proves the anti "description-laundering" invariant (the approver-facing effect is
computed from ground truth, not model prose) and the risk classifier that tiers
human oversight by blast-radius + reversibility.
"""

from __future__ import annotations

from app.domain.canonical import args_digest
from app.proposals.effect import HIGH_BLAST_THRESHOLD, derive_effect


def _e(**kw):
    base = dict(tool_id="case.apply_disposition", tier="write-proposal",
               side_effects="reversible", args={"case_id": "c-1"},
               affected_urns=["wr:t:case:case/c-1"])
    base.update(kw)
    return derive_effect(**base)


def test_blast_radius_is_ground_truth_urn_count_not_model_number():
    # Model claims blast_radius 1, but the intent actually touches 3 URNs.
    e = _e(affected_urns=["a", "b", "c"], model_effect={"blast_radius": 1})
    assert e["blast_radius"] == 3  # server count wins; model cannot understate


def test_authoritative_summary_is_derived_and_model_prose_demoted():
    e = _e(model_effect={"summary": "totally safe, just a tiny tweak"})
    assert e["authoritative_summary"] == (
        "Runs case.apply_disposition (tier write-proposal, reversible); affects 1 resource.")
    assert e["agent_summary"] == "totally safe, just a tiny tweak"  # kept but demoted


def test_args_digest_binds_the_exact_args():
    args = {"case_id": "c-9", "severity": "high"}
    assert _e(args=args)["args_digest"] == args_digest(args)


def test_reversible_single_resource_is_low_risk():
    assert _e()["risk"] == "low"


def test_destructive_is_high_risk_and_irreversible():
    e = _e(side_effects="destructive")
    assert e["reversibility"] == "irreversible"
    assert e["risk"] == "high"


def test_bulk_reversible_write_is_high_risk():
    e = _e(affected_urns=[f"u{i}" for i in range(HIGH_BLAST_THRESHOLD)])
    assert e["risk"] == "high"  # blast-radius class, even though each change reverts


def test_write_direct_tier_is_high_risk():
    assert _e(tier="write-direct")["risk"] == "high"


def test_unknown_side_effects_fails_safe_to_irreversible():
    assert _e(side_effects="")["reversibility"] == "irreversible"
