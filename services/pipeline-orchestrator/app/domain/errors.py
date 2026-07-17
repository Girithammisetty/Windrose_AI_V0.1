"""Domain error envelope (MASTER-FR-024). Each maps to an HTTP status + code."""

from __future__ import annotations


class AppError(Exception):
    status = 500
    code = "INTERNAL"

    def __init__(self, message: str = "", *, details=None, code: str | None = None,
                 status: int | None = None):
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status


class Unauthenticated(AppError):
    status = 401
    code = "UNAUTHENTICATED"


class PermissionDenied(AppError):
    status = 403
    code = "PERMISSION_DENIED"


class NotFound(AppError):
    status = 404
    code = "NOT_FOUND"


class Conflict(AppError):
    status = 409
    code = "CONFLICT"


class ValidationFailed(AppError):
    status = 422
    code = "VALIDATION_FAILED"


class CannotCompile(AppError):
    status = 422
    code = "CANNOT_COMPILE"


class CannotRunPipelineType(AppError):
    status = 422
    code = "CANNOT_RUN_PIPELINE_TYPE"


class TemplateNotRunnable(AppError):
    status = 422
    code = "TEMPLATE_NOT_RUNNABLE"


class RateLimited(AppError):
    status = 429
    code = "RATE_LIMITED"

    def __init__(self, message: str = "", *, retry_after: int = 15, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class BudgetExhausted(AppError):
    status = 429
    code = "BUDGET_EXHAUSTED"


class DependencyUnavailable(AppError):
    status = 503
    code = "DEPENDENCY_UNAVAILABLE"
