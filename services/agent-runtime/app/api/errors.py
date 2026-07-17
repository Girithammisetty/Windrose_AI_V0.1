"""Error envelope + trace middleware (MASTER-FR-024/028) via py-common helper."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from windrose_common.web import register_error_handlers

from app.domain.errors import AppError

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def current_trace_id() -> str | None:
    return _trace_id.get()


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        tid = request.headers.get("x-trace-id") or str(uuid.uuid4())
        token = _trace_id.set(tid)
        try:
            response = await call_next(request)
        finally:
            _trace_id.reset(token)
        response.headers["X-Trace-Id"] = tid
        return response


def install_error_handlers(app) -> None:
    register_error_handlers(app, app_error_cls=AppError, trace_id_fn=current_trace_id)
