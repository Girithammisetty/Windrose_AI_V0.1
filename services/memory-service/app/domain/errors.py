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


class PermissionDenied(AppError):
    code = "PERMISSION_DENIED"
    status = 403


class Unauthenticated(AppError):
    code = "UNAUTHENTICATED"
    status = 401


# --- memory-service specific (BRD §5 errors) --------------------------------


class ScopeDenied(AppError):
    code = "SCOPE_DENIED"
    status = 403


class PiiRejected(AppError):
    code = "PII_REJECTED"
    status = 422


class ScreeningUnavailable(AppError):
    """BR-1: injection classifier down => fail closed on writes (503)."""

    code = "SCREENING_UNAVAILABLE"
    status = 503


class EmbeddingUnavailable(AppError):
    """BR-2: ai-gateway/Ollama embeddings unreachable. Writes queue in mem:pend
    (≤1h) rather than persisting unembedded; retrieval degrades to recency+tag."""

    code = "EMBEDDING_UNAVAILABLE"
    status = 503


class SnapshotExpired(AppError):
    code = "SNAPSHOT_EXPIRED"
    status = 422


class RebuildInProgress(AppError):
    code = "CONFLICT"
    status = 409
