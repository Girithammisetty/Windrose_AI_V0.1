"""Auth middleware: JWT for /api/v1, SPIFFE allowlist for /internal/v1."""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.errors import error_response
from app.domain.errors import AppError, Unauthenticated
from app.utils import uuid7

_PUBLIC_PATHS = {"/healthz", "/readyz", "/metrics", "/docs", "/openapi.json"}

# Internal, unauthenticated service-to-service resolver: query-service calls
# GET /api/v1/datasets/resolve with no bearer token to map a dataset name to its
# physical Iceberg parquet source (QRY-FR-005). Tenant is passed as a query param
# and the route returns only physical-location metadata (row data stays
# RLS-guarded at query time). See app/api/routes/datasets.py::resolve_dataset.
_INTERNAL_UNAUTH_PATHS = {"/api/v1/datasets/resolve"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            path in _PUBLIC_PATHS
            or path in _INTERNAL_UNAUTH_PATHS
            or path.startswith("/internal/")
        ):
            # /internal auth is enforced by the require_internal dependency
            return await call_next(request)
        trace_id = getattr(request.state, "trace_id", str(uuid7()))
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return error_response(401, "UNAUTHENTICATED", "missing bearer token", trace_id)
        try:
            verifier = request.app.state.token_verifier
            request.state.principal = await verifier.verify(auth_header[7:])
        except Unauthenticated as exc:
            return error_response(401, exc.code, exc.message, trace_id)
        except AppError as exc:
            return error_response(exc.status, exc.code, exc.message, trace_id)
        return await call_next(request)
