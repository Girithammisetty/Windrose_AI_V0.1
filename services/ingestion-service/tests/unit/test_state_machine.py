"""Full state-machine matrix (ING-FR-022, §4.3)."""

from __future__ import annotations

import pytest

from app.domain.state_machine import (
    STATUSES,
    TERMINAL_STATUSES,
    GuardFailedError,
    IllegalTransitionError,
    TransitionContext,
    allowed_targets,
    validate_transition,
)

ALLOWED = {
    ("created", "awaiting_upload"),
    ("created", "queued"),
    ("created", "cancelled"),
    ("awaiting_upload", "queued"),
    ("awaiting_upload", "cancelled"),
    ("awaiting_upload", "expired"),
    ("queued", "running"),
    ("queued", "cancelled"),
    ("running", "committing"),
    ("running", "retrying"),
    ("running", "failed"),
    ("running", "cancelled"),
    ("committing", "completed"),
    ("committing", "retrying"),
    ("committing", "failed"),
    ("retrying", "running"),
    ("retrying", "failed"),
}


def passing_ctx() -> TransitionContext:
    return TransitionContext(
        ingestion_mode="file_upload",
        upload_session_opened=True,
        payload_valid=True,
        slot_available=True,
        rows_decoded=10,
        allow_empty=False,
        commit_ok=True,
        attempts=1,
        max_attempts=5,
        error_log_present=True,
        committed=False,
    )


@pytest.mark.parametrize("frm", STATUSES)
@pytest.mark.parametrize("to", STATUSES)
def test_full_transition_matrix(frm: str, to: str) -> None:
    if (frm, to) in ALLOWED:
        validate_transition(frm, to, passing_ctx())  # must not raise
    else:
        with pytest.raises(IllegalTransitionError) as exc:
            validate_transition(frm, to, passing_ctx())
        assert exc.value.status == 409
        assert exc.value.details == {"current_status": frm, "requested": to}


def test_terminal_statuses_have_no_exits() -> None:
    for status in TERMINAL_STATUSES:
        assert allowed_targets(status) == set()


@pytest.mark.parametrize(
    ("frm", "to", "ctx"),
    [
        (
            "created",
            "awaiting_upload",
            TransitionContext(ingestion_mode="query", upload_session_opened=True),
        ),
        ("created", "awaiting_upload", TransitionContext(ingestion_mode="file_upload")),
        ("created", "queued", TransitionContext(payload_valid=False)),
        ("awaiting_upload", "queued", TransitionContext(payload_valid=False)),
        ("queued", "running", TransitionContext(slot_available=False)),
        ("running", "committing", TransitionContext(rows_decoded=0, allow_empty=False)),
        ("committing", "completed", TransitionContext(commit_ok=False)),
        ("running", "retrying", TransitionContext(attempts=5, max_attempts=5)),
        ("committing", "retrying", TransitionContext(attempts=7, max_attempts=5)),
        ("retrying", "failed", TransitionContext(error_log_present=False)),
        ("running", "failed", TransitionContext(error_log_present=False)),
        ("running", "cancelled", TransitionContext(committed=True)),
        ("queued", "cancelled", TransitionContext(committed=True)),
    ],
)
def test_guard_failures_conflict(frm: str, to: str, ctx: TransitionContext) -> None:
    with pytest.raises(GuardFailedError) as exc:
        validate_transition(frm, to, ctx)
    assert exc.value.status == 409
    assert exc.value.details["current_status"] == frm
    assert exc.value.details["requested"] == to


def test_guard_passes() -> None:
    # decode guard: zero rows allowed when allow_empty
    validate_transition(
        "running", "committing", TransitionContext(rows_decoded=0, allow_empty=True)
    )
    # retry allowed below the attempt cap
    validate_transition("running", "retrying", TransitionContext(attempts=4, max_attempts=5))
    # retrying resumes unconditionally
    validate_transition("retrying", "running", TransitionContext())
