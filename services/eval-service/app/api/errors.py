"""Error envelope + trace middleware (MASTER-FR-024/028)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.domain.errors import AppError
from app.utils import uuid7

logger = logging.getLogger(__name__)


def error_response(
    status: int, code: str, message: str, trace_id: str, details=None
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "trace_id": trace_id}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers={"X-Trace-Id": trace_id})


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or str(uuid7())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers.setdefault("X-Trace-Id", trace_id)
        return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        trace_id = getattr(request.state, "trace_id", str(uuid7()))
        return error_response(exc.status, exc.code, exc.message, trace_id, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        trace_id = getattr(request.state, "trace_id", str(uuid7()))
        details = [
            {"field": ".".join(str(p) for p in e.get("loc", [])), "problem": e.get("msg")}
            for e in exc.errors()
        ]
        return error_response(
            422, "VALIDATION_FAILED", "request validation failed", trace_id, details
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        trace_id = getattr(request.state, "trace_id", str(uuid7()))
        logger.exception("unhandled error trace_id=%s", trace_id)
        return error_response(500, "INTERNAL", "internal server error", trace_id)
