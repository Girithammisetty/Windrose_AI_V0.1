"""Auto-execute policy matrix + destructive-never-auto (ART-FR-043, BR-1, AC-5)."""

from __future__ import annotations

import pytest

from app.domain.errors import ValidationFailed
from app.domain.policy import (
    canary_assignment,
    is_auto_execute,
    resolve_version,
    validate_auto_policy,
)

POLICY = {
    "dashboard-designer": {"write-proposal": {"none": "auto", "reversible": "auto",
                                              "destructive": "manual"}},
    "case-triage": {"write-proposal": {"none": "manual", "reversible": "manual",
                                       "destructive": "manual"}},
    "*": {"write-proposal": {"*": "manual"}},
}


def test_reversible_auto_for_configured_agent():
    assert is_auto_execute(POLICY, "dashboard-designer", "write-proposal", "reversible") is True


def test_case_triage_manual():
    assert is_auto_execute(POLICY, "case-triage", "write-proposal", "reversible") is False


def test_destructive_never_auto_even_if_configured():
    bad = {"x": {"write-proposal": {"destructive": "auto"}}}
    # runtime evaluator hard-codes manual regardless of stored config (layer 2)
    assert is_auto_execute(bad, "x", "write-proposal", "destructive") is False


def test_admin_tier_never_auto():
    assert is_auto_execute({"x": {"admin": {"none": "auto"}}}, "x", "admin", "none") is False


def test_default_manual():
    assert is_auto_execute({}, "whatever", "write-proposal", "reversible") is False


def test_validate_rejects_destructive_auto():
    with pytest.raises(ValidationFailed):
        validate_auto_policy({"x": {"write-proposal": {"destructive": "auto"}}})


def test_validate_rejects_admin_auto():
    with pytest.raises(ValidationFailed):
        validate_auto_policy({"x": {"admin": {"none": "auto"}}})


def test_validate_allows_reversible_auto():
    validate_auto_policy({"x": {"write-proposal": {"reversible": "auto"}}})  # no raise


def test_version_resolution_pin_wins():
    assert resolve_version(kill_active=False, pinned_version=14, canary_version=15,
                           default_version=13) == 14


def test_version_resolution_canary_when_no_pin():
    assert resolve_version(kill_active=False, pinned_version=None, canary_version=15,
                           default_version=13) == 15


def test_canary_distribution_deterministic():
    # ~10% assigned; deterministic by seed
    seeds = [f"s-{i}" for i in range(2000)]
    hits = sum(canary_assignment(s, 10) for s in seeds)
    assert 150 <= hits <= 250
    assert canary_assignment("s-1", 10) == canary_assignment("s-1", 10)
