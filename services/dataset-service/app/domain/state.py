"""State machines with guards (BRD §4.3). Illegal transitions raise Conflict (409)."""

from __future__ import annotations

from app.domain.entities import Dataset, DatasetStatus, Profile, ProfileStatus
from app.domain.errors import Conflict

_DATASET_TRANSITIONS: set[tuple[str, str]] = {
    (DatasetStatus.DRAFT, DatasetStatus.PROCESSING),
    (DatasetStatus.PROCESSING, DatasetStatus.READY),
    (DatasetStatus.PROCESSING, DatasetStatus.FAILED),
    (DatasetStatus.FAILED, DatasetStatus.PROCESSING),
    (DatasetStatus.READY, DatasetStatus.PROCESSING),
}

_PROFILE_TRANSITIONS: set[tuple[str, str]] = {
    (ProfileStatus.PENDING, ProfileStatus.RUNNING),
    (ProfileStatus.PENDING, ProfileStatus.FAILED),  # scheduling failure before start
    (ProfileStatus.RUNNING, ProfileStatus.COMPLETED),
    (ProfileStatus.RUNNING, ProfileStatus.FAILED),
    (ProfileStatus.FAILED, ProfileStatus.PENDING),  # manual re-trigger
}


def transition_dataset(
    dataset: Dataset,
    to: str,
    *,
    error_log: dict | None = None,
    has_version: bool = False,
) -> None:
    """Apply a dataset status transition, enforcing V1-preserved invariants (DST-FR-002)."""
    frm = dataset.status
    if frm == to:
        return
    if (frm, to) not in _DATASET_TRANSITIONS:
        raise Conflict(f"illegal dataset transition {frm} -> {to}")
    if to == DatasetStatus.FAILED and not error_log:
        raise Conflict("dataset cannot enter 'failed' without error_log")
    if to == DatasetStatus.READY and not has_version:
        raise Conflict("dataset cannot enter 'ready' without a version")
    dataset.status = to
    if to == DatasetStatus.FAILED:
        dataset.error_log = error_log
    elif frm == DatasetStatus.FAILED:
        dataset.error_log = None


def transition_profile(profile: Profile, to: str) -> None:
    frm = profile.status
    if (frm, to) not in _PROFILE_TRANSITIONS:
        raise Conflict(f"illegal profile transition {frm} -> {to}")
    profile.status = to
