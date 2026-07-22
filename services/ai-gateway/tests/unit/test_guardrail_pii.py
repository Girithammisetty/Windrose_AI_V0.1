"""RegexPIIAnalyzer entity coverage, including ADDRESS/PERSON (BRD 58 SEC-5) —
real, narrow structural patterns, not general NER. No prior test coverage
existed for this analyzer at all."""

from __future__ import annotations

from app.adapters.guardrail_models import RegexPIIAnalyzer

ALL = ["EMAIL", "PHONE", "CREDIT_CARD", "SSN", "IBAN", "ADDRESS", "PERSON"]


def test_detects_email_and_ssn():
    found = RegexPIIAnalyzer().analyze("contact jane@acme.com, ssn 123-45-6789", ALL)
    kinds = {e.kind for e in found}
    assert "EMAIL" in kinds and "SSN" in kinds


def test_detects_street_address():
    found = RegexPIIAnalyzer().analyze("send it to 742 Evergreen Terrace please", ALL)
    addr = [e for e in found if e.kind == "ADDRESS"]
    assert len(addr) == 1
    assert addr[0].text == "742 Evergreen Terrace"


def test_detects_honorific_person_name():
    found = RegexPIIAnalyzer().analyze("Dr. Jane Smith signed off", ALL)
    person = [e for e in found if e.kind == "PERSON"]
    assert len(person) == 1
    assert person[0].text == "Dr. Jane Smith"


def test_person_pattern_does_not_match_plain_text():
    # The floor is narrow by design -- a bare capitalized name with no
    # honorific is NOT caught (that still needs NER, unchanged limitation).
    found = RegexPIIAnalyzer().analyze("Jane Smith called about the claim", ALL)
    assert not [e for e in found if e.kind == "PERSON"]


def test_unrequested_entities_are_never_returned():
    found = RegexPIIAnalyzer().analyze("Dr. Jane Smith, jane@acme.com", ["EMAIL"])
    assert {e.kind for e in found} == {"EMAIL"}
