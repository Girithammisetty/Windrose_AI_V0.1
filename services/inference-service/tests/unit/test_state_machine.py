"""Job state-machine unit tests (BRD §4.3)."""

from __future__ import annotations

from app.domain.enums import JobStatus
from app.domain.state import can_transition, is_cancellable, is_terminal


def test_happy_path_transitions():
    assert can_transition(JobStatus.validating, JobStatus.queued)
    assert can_transition(JobStatus.queued, JobStatus.submitted)
    assert can_transition(JobStatus.submitted, JobStatus.running)
    assert can_transition(JobStatus.running, JobStatus.finalizing)
    assert can_transition(JobStatus.finalizing, JobStatus.succeeded)


def test_terminal_states_immutable():
    for terminal in (JobStatus.succeeded, JobStatus.failed, JobStatus.rejected,
                     JobStatus.cancelled):
        assert is_terminal(terminal)
        assert not can_transition(terminal, JobStatus.running)


def test_cancellable_only_queued_submitted_running():
    assert is_cancellable(JobStatus.queued)
    assert is_cancellable(JobStatus.submitted)
    assert is_cancellable(JobStatus.running)
    assert not is_cancellable(JobStatus.validating)
    assert not is_cancellable(JobStatus.finalizing)


def test_invalid_transitions_rejected():
    assert not can_transition(JobStatus.validating, JobStatus.running)
    assert not can_transition(JobStatus.queued, JobStatus.finalizing)
    assert not can_transition(JobStatus.succeeded, JobStatus.finalizing)
