"""Domain error hierarchy mapped to the master error envelope (MASTER-FR-024, BRD §4.5)."""

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


class SnapshotAlreadyRegistered(Conflict):
    """A version for this snapshot already exists — a safe idempotent skip for
    event consumers, distinct from BR-1 'snapshot not yet readable' (which must
    be retried). Still surfaces as 409 CONFLICT on the API."""


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
