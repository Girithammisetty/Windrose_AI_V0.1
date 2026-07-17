"""Auth middleware.

- `/api/v1/*` admin plane: platform JWT in `Authorization: Bearer` (or an
  allowed SPIFFE identity for the service key-mint path, AIG-FR-032).
- `/v1/*` data plane: virtual key in `Authorization: Bearer nk-…` + platform
  JWT in `X-Windrose-JWT` (AIG-FR-001). Tenant identity comes exclusively from
  the JWT (AIG-FR-002); any `x-windrose-tenant-id` header is ignored."""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.errors import error_response
from app.domain.errors import AppError, KeyInvalid, Unauthenticated
from app.utils import uuid7

_PUBLIC_PATHS = {"/healthz", "/readyz", "/metrics", "/docs", "/openapi.json"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)
        trace_id = getattr(request.state, "trace_id", str(uuid7()))
        try:
            if path.startswith("/v1/"):
                await self._data_plane(request)
            else:
                await self._admin_plane(request)
        except AppError as exc:
            return error_response(exc.status, exc.code, exc.message, trace_id,
                                  exc.details)
        return await call_next(request)

    async def _admin_plane(self, request: Request) -> None:
        from app.api.auth import is_internal

        spiffe = is_internal(request)
        if spiffe:
            request.state.spiffe = spiffe
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            verifier = request.app.state.token_verifier
            request.state.principal = await verifier.verify(auth_header[7:])
        elif not spiffe:
            raise Unauthenticated("missing bearer token")

    async def _data_plane(self, request: Request) -> None:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            raise KeyInvalid("missing virtual key")
        secret = auth_header[7:]
        key_service = request.app.state.key_service
        request.state.virtual_key = await key_service.authenticate(secret)

        jwt_token = request.headers.get("x-windrose-jwt", "")
        if not jwt_token:
            raise Unauthenticated("missing X-Windrose-JWT")
        verifier = request.app.state.token_verifier
        principal = await verifier.verify(jwt_token)
        if principal.tenant_id != request.state.virtual_key.tenant_id:
            # tenant is taken from the verified JWT only; a key from another
            # tenant is simply invalid (no resource-existence leak).
            raise KeyInvalid("virtual key is invalid or revoked")
        request.state.principal = principal
