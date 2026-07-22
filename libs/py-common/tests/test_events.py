"""Unit tests for datacern_common.events.validate_envelope (WS5, BRD 58)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from datacern_common.events import validate_envelope


def _envelope(**overrides) -> dict:
    base = {
        "event_id": str(uuid.uuid4()),
        "event_type": "case.created",
        "tenant_id": str(uuid.uuid4()),
        "actor": {"type": "user", "id": "u-1"},
        "via_agent": None,
        "resource_urn": "wr:t:case:c-1",
        "occurred_at": datetime.now(UTC).isoformat(),
        "trace_id": "trace-1",
        "payload": {},
    }
    base.update(overrides)
    return base


def test_accepts_well_formed_envelope():
    validate_envelope(_envelope())  # must not raise


@pytest.mark.parametrize("actor_type", ["user", "service", "agent", "platform"])
def test_accepts_every_master_actor_type(actor_type):
    validate_envelope(_envelope(actor={"type": actor_type, "id": "x"}))


def test_rejects_unknown_actor_type():
    with pytest.raises(ValueError):
        validate_envelope(_envelope(actor={"type": "system", "id": "x"}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_id": ""},
        {"event_type": ""},
        {"tenant_id": ""},
        {"actor": {"type": "", "id": "x"}},
        {"actor": {"type": "user", "id": ""}},
        {"occurred_at": ""},
    ],
)
def test_rejects_missing_required_fields(overrides):
    with pytest.raises(ValueError):
        validate_envelope(_envelope(**overrides))


def test_rejects_non_object_payload():
    with pytest.raises(ValueError):
        validate_envelope(_envelope(payload="{}"))
