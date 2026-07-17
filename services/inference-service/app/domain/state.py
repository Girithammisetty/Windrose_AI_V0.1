"""Job state machine (BRD §4.3): allowed transitions + guards.

    validating ──invalid──▶ rejected
        │ valid
     queued ──capacity──▶ submitted ──▶ running ──▶ finalizing ──▶ succeeded
        │                    │            │  │                     └▶ failed
        │                    └── failed ──┴──┼──▶ failed
        └── cancel ──▶ cancelled   cancel ──▶ cancelling ──▶ cancelled
"""

from __future__ import annotations

from app.domain.enums import CANCELLABLE, TERMINAL, JobStatus

# Allowed forward transitions (source -> {targets}).
_ALLOWED: dict[JobStatus, set[JobStatus]] = {
    JobStatus.validating: {JobStatus.rejected, JobStatus.queued, JobStatus.cancelled},
    JobStatus.queued: {JobStatus.submitted, JobStatus.cancelled, JobStatus.failed},
    JobStatus.submitted: {
        JobStatus.running,
        JobStatus.failed,
        JobStatus.cancelling,
        JobStatus.cancelled,
    },
    JobStatus.running: {
        JobStatus.finalizing,
        JobStatus.failed,
        JobStatus.cancelling,
    },
    JobStatus.finalizing: {JobStatus.succeeded, JobStatus.failed},
    JobStatus.cancelling: {JobStatus.cancelled, JobStatus.finalizing, JobStatus.failed},
}


def can_transition(src: int, dst: int) -> bool:
    s, d = JobStatus(src), JobStatus(dst)
    if s in TERMINAL:
        return False
    return d in _ALLOWED.get(s, set())


def is_cancellable(status: int) -> bool:
    return JobStatus(status) in CANCELLABLE


def is_terminal(status: int) -> bool:
    return JobStatus(status) in TERMINAL


class InvalidTransition(Exception):
    def __init__(self, src: int, dst: int):
        super().__init__(
            f"invalid transition {JobStatus(src).name} -> {JobStatus(dst).name}"
        )
        self.src = src
        self.dst = dst
