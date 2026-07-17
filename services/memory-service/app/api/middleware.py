"""Auth middleware: JWT for /api/v1, SPIFFE allowlist for /internal/v1."""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.errors import error_response
from app.domain.errors import AppError, Unauthenticated
from app.utils import uuid7

_PUBLIC_PATHS = {"/healthz", "/readyz", "/metrics", "/docs", "/openapi.json"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/internal/"):
            return await call_next(request)
        trace_id = getattr(request.state, "trace_id", str(uuid7()))
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return error_response(401, "UNAUTHENTICATED", "missing bearer token", trace_id)
        try:
            request.state.principal = await request.app.state.token_verifier.verify(
                auth_header[7:])
        except Unauthenticated as exc:
            return error_response(401, exc.code, exc.message, trace_id)
        except AppError as exc:
            return error_response(exc.status, exc.code, exc.message, trace_id)
        return await call_next(request)
