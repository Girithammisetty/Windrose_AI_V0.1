"""Request authN (MASTER-FR-010/011). Verifies the incoming user/agent JWT via
the container's JWKS/static verifier and yields a Principal. session_id alone
never authorizes (BR-11) — the tenant always comes from the verified token."""

from __future__ import annotations

from fastapi import Request
from windrose_common.authjwt import InvalidTokenError, Principal

from app.domain.errors import Unauthorized


async def principal_of(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    verifier = request.app.state.container.token_verifier
    try:
        return await verifier.verify(token)
    except InvalidTokenError as exc:
        raise Unauthorized(str(exc)) from exc


def is_operator(principal: Principal) -> bool:
    return "operator" in principal.scopes or "platform.admin" in principal.scopes


def is_tenant_admin(principal: Principal) -> bool:
    return is_operator(principal) or "tenant.admin" in principal.scopes
