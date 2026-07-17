"""State machines (BRD 06 §4.2). Transitions raise Conflict when illegal."""

from __future__ import annotations

from app.domain.errors import Conflict

MODEL_VERSION_TRANSITIONS: dict[tuple[str, str], str] = {
    ("draft", "in_review"): "submit",
    ("in_review", "published"): "approve",
    ("in_review", "rejected"): "reject",
    ("rejected", "draft"): "revise",
    ("published", "superseded"): "supersede",
}

VERIFIED_QUERY_TRANSITIONS: dict[tuple[str, str], str] = {
    ("draft", "pending_review"): "submit",
    ("pending_review", "approved"): "approve",
    ("pending_review", "rejected"): "reject",
    ("rejected", "draft"): "revise",
    ("approved", "pending_review"): "revalidate",  # SEM-FR-043 schema break
    ("draft", "archived"): "archive",
    ("pending_review", "archived"): "archive",
    ("approved", "archived"): "archive",
    ("rejected", "archived"): "archive",
}


def check_version_transition(current: str, target: str) -> None:
    if (current, target) not in MODEL_VERSION_TRANSITIONS:
        raise Conflict(f"illegal model version transition {current} -> {target}")


def check_vq_transition(current: str, target: str) -> None:
    if (current, target) not in VERIFIED_QUERY_TRANSITIONS:
        raise Conflict(f"illegal verified query transition {current} -> {target}")
