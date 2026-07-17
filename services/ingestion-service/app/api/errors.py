"""Error envelope handlers (MASTER-FR-024): {error: {code, message, details?, trace_id}}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.context import current_trace_id
from app.domain.errors import AppError

logger = logging.getLogger("ingestion.errors")


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "trace_id": current_trace_id()}
    if details is not None:
        error["details"] = details
    return {"error": error}


def _response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=error_body(code, message, details),
        headers={"X-Trace-Id": current_trace_id()},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_req: Request, exc: AppError) -> JSONResponse:
        return _response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(p) for p in err["loc"] if p not in ("body",)),
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return _response(422, "VALIDATION_FAILED", "request validation failed", details)

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return _response(500, "INTERNAL", "internal error")
