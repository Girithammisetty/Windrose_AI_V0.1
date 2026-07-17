"""Role-grounding helpers (ART-FR-040) — persona label + tone directive."""

from __future__ import annotations

from app.graphs.persona import caller_persona, role_directive


def test_caller_persona_prefers_the_callers_primary_role():
    caller = {"roles": ["Case Analyst", "Insights User"], "capabilities": []}
    assert caller_persona(caller, {"persona": "claims adjuster"}) == "Case Analyst"


def test_caller_persona_falls_back_to_tenant_persona_then_default():
    assert caller_persona(None, {"persona": "claims adjuster"}) == "claims adjuster"
    assert caller_persona({"roles": []}, {}) == "domain user"


def test_role_directive_operational_role_gets_plain_language():
    d = role_directive({"roles": ["Case Analyst"]})
    assert "Case Analyst" in d
    assert "plain" in d.lower() and "non-technical" in d.lower()


def test_role_directive_technical_role_gets_technical_language():
    d = role_directive({"roles": ["Model Builder"]})
    assert "Model Builder" in d
    assert "technical" in d.lower()


def test_role_directive_empty_when_role_unknown():
    assert role_directive(None) == ""
    assert role_directive({"roles": []}) == ""
