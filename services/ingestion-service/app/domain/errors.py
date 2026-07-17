"""Error catalog (MASTER-FR-024, BRD 03 §4.4) and internal job-failure exceptions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    """ING-FR-080 categorized ingestion errors."""

    SOURCE_UNREACHABLE = "SOURCE_UNREACHABLE"
    AUTH_FAILED = "AUTH_FAILED"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    DECODE_ERROR = "DECODE_ERROR"
    ROW_LIMIT_EXCEEDED = "ROW_LIMIT_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    INTERNAL = "INTERNAL"


class AppError(Exception):
    """API error rendered as {error: {code, message, details?, trace_id}}."""

    code = "INTERNAL"
    status = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.details = details


class ValidationFailedError(AppError):
    code = "VALIDATION_FAILED"
    status = 422


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status = 404

    def __init__(self, message: str = "resource not found", **kw: Any) -> None:
        super().__init__(message, **kw)


class ConflictError(AppError):
    code = "CONFLICT"
    status = 409


class PermissionDeniedError(AppError):
    code = "PERMISSION_DENIED"
    status = 403


class UnauthenticatedError(AppError):
    code = "UNAUTHENTICATED"
    status = 401


class ConnectionTestFailedError(AppError):
    code = "CONNECTION_TEST_FAILED"
    status = 424

    def __init__(
        self, message: str, *, error_category: str, error_detail: str | None = None
    ) -> None:
        super().__init__(
            message, details={"error_category": error_category, "error_detail": error_detail}
        )
        self.error_category = error_category


class UploadExpiredError(AppError):
    code = "UPLOAD_EXPIRED"
    status = 410


class ChecksumMismatchError(AppError):
    code = "CHECKSUM_MISMATCH"
    status = 422


class PayloadTooLargeError(AppError):
    code = "PAYLOAD_TOO_LARGE"
    status = 413


class SignatureInvalidError(AppError):
    code = "SIGNATURE_INVALID"
    status = 401


class RateLimitedError(AppError):
    code = "RATE_LIMITED"
    status = 429


class RequestTimeoutError(AppError):
    code = "TIMEOUT"
    status = 408


class UnsupportedConnectorError(AppError):
    """A connector type is declared in the catalog but has no real driver wired
    in this deployment. Raised at connection create/test/preview time so a
    driverless connector can never fake a successful probe (422, honest)."""

    code = "UNSUPPORTED_CONNECTOR"
    status = 422

    def __init__(self, connector_type: str) -> None:
        super().__init__(
            f"connector type {connector_type!r}: driver not available in this deployment",
            details=[
                {
                    "field": "connector_type",
                    "message": f"no driver wired for {connector_type!r}; "
                    "choose a supported connector type",
                }
            ],
        )
        self.connector_type = connector_type


class NotImplementedFeatureError(AppError):
    """Feature exists in the API surface but its backend is not implemented.
    Rejects the request honestly (501) instead of accepting work that would
    silently never happen."""

    code = "NOT_IMPLEMENTED"
    status = 501


# --- internal job execution failures (never rendered directly) -----------------


class TransientSourceError(Exception):
    """Retryable failure (ING-FR-081)."""

    def __init__(self, category: ErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


class PermanentJobError(Exception):
    """Non-retryable failure; job transitions straight to `failed`."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        samples: list[dict[str, Any]] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.samples = samples or []
        self.hint = hint
