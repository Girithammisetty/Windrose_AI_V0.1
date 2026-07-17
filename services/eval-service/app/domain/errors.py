"""Domain error hierarchy mapped to the master error envelope (MASTER-FR-024)."""

from __future__ import annotations


class AppError(Exception):
    code = "INTERNAL"
    status = 500

    def __init__(self, message: str, details: list | dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class ValidationFailed(AppError):
    code = "VALIDATION_FAILED"
    status = 422


class NotFound(AppError):
    code = "NOT_FOUND"
    status = 404

    def __init__(self, message: str = "resource not found", details=None):
        super().__init__(message, details)


class Conflict(AppError):
    code = "CONFLICT"
    status = 409


class Gone(AppError):
    code = "GONE"
    status = 410


class PermissionDenied(AppError):
    code = "PERMISSION_DENIED"
    status = 403


class Unauthenticated(AppError):
    code = "UNAUTHENTICATED"
    status = 401


# ---- domain-specific error codes (BRD §5 notable errors) ----------------------


class AnonymizationRequired(ValidationFailed):
    """Promotion of a production-sourced case without attestation (BR-3, AC-5)."""

    code = "ANONYMIZATION_REQUIRED"


class JudgeGatesAlone(ValidationFailed):
    """Suite gate rule lacks a deterministic scorer term (BR-1, AC-3)."""

    code = "JUDGE_GATES_ALONE"


class BaselineIncomparable(Conflict):
    """Baseline pins mismatch the candidate's dataset/scorer versions (BR-2, AC-7)."""

    code = "BASELINE_INCOMPARABLE"


class EvalBudgetExceeded(AppError):
    """Cumulative run cost exceeded its cap (EVL-FR-023, AC-13)."""

    code = "EVAL_BUDGET_EXCEEDED"
    status = 402


class JudgeAgreementTooLow(ValidationFailed):
    """Judge calibration agreement < 0.8 blocks activation (EVL-FR-014, AC-11)."""

    code = "JUDGE_AGREEMENT_TOO_LOW"


class FrozenDataset(Conflict):
    """Mutation attempted against a frozen dataset version (AC-15)."""

    code = "DATASET_FROZEN"
