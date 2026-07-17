"""State machines: run status (from pipeline events) and model-version stage
(governed promotion) — BRD §4.5, EXP-FR-003/004/032."""

from __future__ import annotations

from app.domain.entities import RUN_STATUS, STAGE
from app.domain.errors import Conflict, ValidationFailed

# Pipeline event type -> target run status (EXP-FR-003).
PIPELINE_EVENT_STATUS = {
    "pipeline.run.submitted": RUN_STATUS["scheduled"],
    "pipeline.run.started": RUN_STATUS["running"],
    "pipeline.run.succeeded": RUN_STATUS["finished"],
    "pipeline.run.failed": RUN_STATUS["failed"],
    "pipeline.run.cancelled": RUN_STATUS["killed"],
}

# Allowed run-status forward transitions (monotonic; terminal states are sinks).
_RUN_ORDER = {0: 0, 1: 1, 2: 2, 3: 2, 4: 2}


def can_transition_run(current: int, target: int) -> bool:
    if current == target:
        return True
    # Never move backwards, never leave a terminal state (finished/failed/killed).
    if current in (RUN_STATUS["finished"], RUN_STATUS["failed"], RUN_STATUS["killed"]):
        return False
    return _RUN_ORDER[target] >= _RUN_ORDER[current]


# Model-version stage transitions (BRD §4.5).
_STAGE_TRANSITIONS: dict[int, set[int]] = {
    STAGE["none"]: {STAGE["staging"], STAGE["archived"]},
    STAGE["staging"]: {STAGE["production"], STAGE["archived"]},
    STAGE["production"]: {STAGE["archived"]},
    STAGE["archived"]: {STAGE["staging"]},  # reinstate via new approval
}


def validate_stage_transition(from_stage: int, target_stage: int) -> None:
    if target_stage not in STAGE.values():
        raise ValidationFailed("unknown target_stage")
    if from_stage == target_stage:
        raise Conflict("model version already at the requested stage")
    if target_stage not in _STAGE_TRANSITIONS.get(from_stage, set()):
        from app.domain.entities import STAGE_LABELS

        raise ValidationFailed(
            f"illegal stage transition {STAGE_LABELS[from_stage]} -> "
            f"{STAGE_LABELS[target_stage]}"
        )
