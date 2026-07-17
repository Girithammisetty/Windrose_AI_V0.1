"""Domain errors mapped to the MASTER-FR-024 envelope + BRD 12 codes."""

from __future__ import annotations


class AppError(Exception):
    status = 500
    code = "INTERNAL"

    def __init__(self, message: str, details: dict | list | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class Unauthenticated(AppError):
    status, code = 401, "UNAUTHENTICATED"


class KeyInvalid(AppError):
    status, code = 401, "KEY_INVALID"


class PermissionDenied(AppError):
    status, code = 403, "PERMISSION_DENIED"


class LadderCap(AppError):
    status, code = 403, "LADDER_CAP"


class NotFound(AppError):
    status, code = 404, "NOT_FOUND"


class Conflict(AppError):
    status, code = 409, "CONFLICT"


class ValidationFailed(AppError):
    status, code = 422, "VALIDATION_FAILED"


class BudgetExhausted(AppError):
    status, code = 402, "BUDGET_EXHAUSTED"


class GuardrailBlocked(AppError):
    status, code = 422, "GUARDRAIL_BLOCKED"


class RateLimited(AppError):
    status, code = 429, "RATE_LIMITED"

    def __init__(self, message: str, retry_after: int = 1, details=None):
        super().__init__(message, details)
        self.retry_after = retry_after


class OutputSchemaInvalid(AppError):
    status, code = 502, "OUTPUT_SCHEMA_INVALID"


class UpstreamUnavailable(AppError):
    status, code = 503, "UPSTREAM_UNAVAILABLE"


class DependencyUnavailable(AppError):
    """Budget backends down — fail closed (BR-14)."""

    status, code = 503, "DEPENDENCY_UNAVAILABLE"


class RejectedAt(Exception):
    """Internal wrapper tagging the pipeline stage that rejected (§7)."""

    def __init__(self, stage: str, error: AppError):
        super().__init__(stage)
        self.stage = stage
        self.error = error
