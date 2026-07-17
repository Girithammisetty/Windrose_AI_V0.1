"""Shared HTTP contract helpers (MASTER-FR-022/024): the error envelope and
cursor pagination, lifted verbatim from the services' vendored copies so the
behaviour is identical. Framework-agnostic core plus a FastAPI wiring helper.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable
from typing import Any

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class CursorError(ValueError):
    """Raised for a malformed cursor or out-of-range limit."""


def error_body(code: str, message: str, trace_id: str | None, details: Any = None) -> dict:
    """MASTER-FR-024: {error: {code, message, details?, trace_id}}."""
    error: dict[str, Any] = {"code": code, "message": message, "trace_id": trace_id}
    if details is not None:
        error["details"] = details
    return {"error": error}


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1 or limit > MAX_LIMIT:
        raise CursorError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def encode_cursor(last_id: str) -> str:
    return base64.urlsafe_b64encode(last_id.encode()).decode()


def decode_cursor(cursor: str) -> str:
    """Decode a base64url cursor. The payload must be a UUID (UUIDv7 ids are
    time-ordered, so the cursor is the previous page's last id)."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        uuid.UUID(raw)
        return raw
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CursorError("malformed cursor") from exc


def page_envelope(next_cursor: str | None, has_more: bool) -> dict:
    return {"next_cursor": next_cursor, "has_more": has_more}


def register_error_handlers(
    app,
    *,
    app_error_cls: type[Exception],
    trace_id_fn: Callable[[], str | None],
    logger=None,
) -> None:
    """Wire the standard error envelope onto a FastAPI app.

    ``app_error_cls`` is the service's domain error base (exposing ``.status``,
    ``.code``, ``.message``, ``.details``); ``trace_id_fn`` returns the current
    request trace id (MASTER-FR-028).
    """
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    def _response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
        trace_id = trace_id_fn()
        return JSONResponse(
            status_code=status,
            content=error_body(code, message, trace_id, details),
            headers={"X-Trace-Id": trace_id or ""},
        )

    @app.exception_handler(app_error_cls)
    async def _app_error(_req: Request, exc):  # type: ignore[no-untyped-def]
        return _response(exc.status, exc.code, exc.message, getattr(exc, "details", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        details = [
            {
                "field": ".".join(str(p) for p in err["loc"] if p not in ("body",)),
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return _response(422, "VALIDATION_FAILED", "request validation failed", details)

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):
        if logger is not None:
            logger.exception("unhandled error", exc_info=exc)
        return _response(500, "INTERNAL", "internal error")
