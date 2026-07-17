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


class RunNotFinished(ValidationFailed):
    code = "RUN_NOT_FINISHED"


class ModelTypeMismatch(ValidationFailed):
    code = "MODEL_TYPE_MISMATCH"


class NotFound(AppError):
    code = "NOT_FOUND"
    status = 404

    def __init__(self, message: str = "resource not found", details=None):
        super().__init__(message, details)


class Conflict(AppError):
    code = "CONFLICT"
    status = 409


class SelfApprovalForbidden(AppError):
    code = "SELF_APPROVAL_FORBIDDEN"
    status = 403


class Gone(AppError):
    code = "GONE"
    status = 410


class PermissionDenied(AppError):
    code = "PERMISSION_DENIED"
    status = 403


class RateLimited(AppError):
    code = "RATE_LIMITED"
    status = 429


class Unauthenticated(AppError):
    code = "UNAUTHENTICATED"
    status = 401


class DependencyUnavailable(AppError):
    code = "DEPENDENCY_UNAVAILABLE"
    status = 503
