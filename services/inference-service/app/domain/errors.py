"""Domain error hierarchy mapped to the master error envelope (MASTER-FR-024).

Includes the inference failure taxonomy (INF-FR-041) as concrete codes.
"""

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
    code = "BUDGET_EXHAUSTED"
    status = 429


class Unauthenticated(AppError):
    code = "UNAUTHENTICATED"
    status = 401


class DependencyUnavailable(AppError):
    code = "DEPENDENCY_UNAVAILABLE"
    status = 503


class NotImplementedYet(AppError):
    """Reserved-namespace 501 (INF-FR-070 online serving)."""

    code = "NOT_IMPLEMENTED"
    status = 501


# --- domain-specific 422s carrying the failure taxonomy (INF-FR-041) ---


class SchemaIncompatible(ValidationFailed):
    code = "SCHEMA_INCOMPATIBLE"


class ModelStageDenied(ValidationFailed):
    code = "MODEL_STAGE_DENIED"


class OutputNotOwned(ValidationFailed):
    code = "OUTPUT_NOT_OWNED"


class EmptyInput(ValidationFailed):
    code = "EMPTY_INPUT"
