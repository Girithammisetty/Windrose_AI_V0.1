"""Domain error hierarchy mapped to the master envelope + BRD §4.3 catalog."""

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


class PermissionDenied(AppError):
    code = "PERMISSION_DENIED"
    status = 403


class RateLimited(AppError):
    code = "RATE_LIMITED"
    status = 429


class Unauthenticated(AppError):
    code = "UNAUTHENTICATED"
    status = 401


# --- Semantic-service specific (BRD §4.3) -----------------------------------


class UnknownMetric(AppError):
    code = "UNKNOWN_METRIC"
    status = 422


class UnknownDimension(AppError):
    code = "UNKNOWN_DIMENSION"
    status = 422


class UnknownGrain(AppError):
    code = "UNKNOWN_GRAIN"
    status = 422


class ExpressionNotAllowed(AppError):
    code = "EXPRESSION_NOT_ALLOWED"
    status = 422


class AmbiguousJoinPath(AppError):
    code = "AMBIGUOUS_JOIN_PATH"
    status = 422


class ModelNotPublished(AppError):
    code = "MODEL_NOT_PUBLISHED"
    status = 409


class ModelUnhealthy(AppError):
    code = "MODEL_UNHEALTHY"
    status = 409


class LimitExceeded(AppError):
    code = "LIMIT_EXCEEDED"
    status = 422
