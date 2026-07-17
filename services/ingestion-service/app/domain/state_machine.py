"""Ingestion status state machine (ING-FR-022, BRD 03 §4.3).

Pure domain logic: guarded transition validation. Persistence (transition rows,
outbox events) is applied by the transition recorder in
app/domain/services/transitions.py within the same DB transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.domain.errors import ConflictError

STATUSES: tuple[str, ...] = (
    "created",
    "awaiting_upload",
    "queued",
    "running",
    "committing",
    "retrying",
    "completed",
    "failed",
    "cancelled",
    "expired",
)

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "expired"})


@dataclass(slots=True)
class TransitionContext:
    """Facts the guards evaluate. Callers set only what is relevant."""

    ingestion_mode: str | None = None
    upload_session_opened: bool = False
    payload_valid: bool = False
    slot_available: bool = False
    rows_decoded: int = 0
    allow_empty: bool = False
    commit_ok: bool = False
    attempts: int = 0
    max_attempts: int = 5
    error_log_present: bool = False
    committed: bool = False  # an Iceberg snapshot has been committed


Guard = Callable[[TransitionContext], tuple[bool, str]]


def _g_upload_opened(c: TransitionContext) -> tuple[bool, str]:
    ok = c.ingestion_mode == "file_upload" and c.upload_session_opened
    return ok, "file_upload mode with an open upload session required"


def _g_payload_valid(c: TransitionContext) -> tuple[bool, str]:
    return c.payload_valid, "payload validation must pass"


def _g_slot(c: TransitionContext) -> tuple[bool, str]:
    return c.slot_available, "tenant concurrency slot required (ING-FR-082)"


def _g_rows(c: TransitionContext) -> tuple[bool, str]:
    return (c.rows_decoded >= 1 or c.allow_empty), ">=1 decoded row or allow_empty=true required"


def _g_commit_ok(c: TransitionContext) -> tuple[bool, str]:
    return c.commit_ok, "single atomic Iceberg commit must succeed"


def _g_attempts(c: TransitionContext) -> tuple[bool, str]:
    return c.attempts < c.max_attempts, "retry attempts exhausted"


def _g_always(_: TransitionContext) -> tuple[bool, str]:
    return True, ""


def _g_error_log(c: TransitionContext) -> tuple[bool, str]:
    return c.error_log_present, "error_log must be populated"


def _g_uncommitted(c: TransitionContext) -> tuple[bool, str]:
    return not c.committed, "only uncommitted jobs can be cancelled"


TRANSITIONS: dict[tuple[str, str], Guard] = {
    ("created", "awaiting_upload"): _g_upload_opened,
    ("created", "queued"): _g_payload_valid,
    ("awaiting_upload", "queued"): _g_payload_valid,
    ("queued", "running"): _g_slot,
    ("running", "committing"): _g_rows,
    ("committing", "completed"): _g_commit_ok,
    ("running", "retrying"): _g_attempts,
    ("committing", "retrying"): _g_attempts,
    ("retrying", "running"): _g_always,
    ("retrying", "failed"): _g_error_log,
    # §4.3 guard note on running->committing: decode guard failure routes to failed;
    # commit-phase permanent failures also land in failed.
    ("running", "failed"): _g_error_log,
    ("committing", "failed"): _g_error_log,
    ("created", "cancelled"): _g_uncommitted,
    ("awaiting_upload", "cancelled"): _g_uncommitted,
    ("queued", "cancelled"): _g_uncommitted,
    ("running", "cancelled"): _g_uncommitted,
    ("awaiting_upload", "expired"): _g_always,
}


class IllegalTransitionError(ConflictError):
    def __init__(self, current: str, requested: str) -> None:
        super().__init__(
            f"illegal transition {current} -> {requested}",
            details={"current_status": current, "requested": requested},
        )


class GuardFailedError(ConflictError):
    def __init__(self, current: str, requested: str, reason: str) -> None:
        super().__init__(
            f"transition {current} -> {requested} rejected: {reason}",
            details={"current_status": current, "requested": requested, "guard": reason},
        )


def allowed_targets(from_status: str) -> set[str]:
    return {to for (frm, to) in TRANSITIONS if frm == from_status}


def validate_transition(from_status: str, to_status: str, ctx: TransitionContext) -> None:
    """Raise ConflictError (409) if the transition is illegal or its guard fails."""
    guard = TRANSITIONS.get((from_status, to_status))
    if guard is None:
        raise IllegalTransitionError(from_status, to_status)
    ok, reason = guard(ctx)
    if not ok:
        raise GuardFailedError(from_status, to_status, reason)
