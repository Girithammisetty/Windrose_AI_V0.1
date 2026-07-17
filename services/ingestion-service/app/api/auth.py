"""JWT verification (MASTER-FR-010/011/014) — vendored per wave-1 rule.

RS256 only; `alg=none` is structurally impossible (allow-list). Verification
uses a configured static public key (unit tier) or a real cached JWKS refresh
from identity-service (runtime, JWKSKeyProvider via windrose_common).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import jwt
from fastapi import Request

from app.config import Settings
from app.domain.errors import PermissionDeniedError, UnauthenticatedError

ALLOWED_ALGS = ["RS256"]  # MASTER-FR-014: alg=none forbidden
PRINCIPAL_TYPES = {"user", "service", "agent_obo", "agent_autonomous"}


@dataclass(slots=True)
class Principal:
    sub: str
    tenant_id: str
    typ: str
    scopes: list[str] = field(default_factory=list)
    agent_id: str | None = None
    agent_version: str | None = None
    obo_sub: str | None = None
    workspace_id: str | None = None

    def actor(self) -> dict[str, str]:
        """MASTER-FR-041 dual attribution: actor + via_agent."""
        if self.typ == "agent_autonomous":
            return {"type": "agent", "id": self.agent_id or self.sub}
        if self.typ == "agent_obo":
            return {"type": "user", "id": self.obo_sub or self.sub}
        return {"type": self.typ, "id": self.sub}

    @property
    def effective_user(self) -> str:
        """Identity whose permission projection governs this request. An
        agent_obo token acts WITH THE USER'S permissions (§8.9), so authz keys
        on obo_sub, not the agent principal (which carries no content grants).
        Mirrors experiment/memory-service; using ``sub`` 403's every agent_obo
        grounding call (e.g. the onboarding agent's connection reads)."""
        if self.typ == "agent_obo" and self.obo_sub:
            return self.obo_sub
        return self.sub

    def via_agent(self) -> dict[str, str] | None:
        if self.typ.startswith("agent") and self.agent_id:
            return {"agent_id": self.agent_id, "version": self.agent_version or "unknown"}
        return None


class JWKSKeyProvider:
    """Real cached JWKS provider via the shared ``windrose_common`` JwksCache:
    fetches identity-service's JWKS over HTTP and caches keys by ``kid`` with a
    TTL refresh (MASTER-FR-010). Runtime key provider for the JWKS auth path."""

    def __init__(self, jwks_url: str, ttl_seconds: int = 300) -> None:
        from windrose_common.authjwt import JwksCache

        self.jwks_url = jwks_url
        self._cache = JwksCache(jwks_url, ttl_seconds=ttl_seconds)

    async def get_key(self, kid: str | None):
        return await self._cache.get_key(kid)


async def verify_token_async(
    token: str, settings: Settings, jwks: JWKSKeyProvider | None = None
) -> Principal:
    """Async verify supporting the real JWKS path (MASTER-FR-010). Falls back to
    the static-PEM sync verifier when no JWKS is configured."""
    if settings.jwt_public_key_pem or jwks is None:
        return verify_token(token, settings)
    from windrose_common.authjwt import InvalidTokenError, JwtVerifier

    verifier = JwtVerifier(
        issuer=settings.jwt_issuer, audience=settings.jwt_audience, jwks=jwks._cache
    )
    try:
        principal = await verifier.verify(token)
    except InvalidTokenError as exc:
        raise UnauthenticatedError(str(exc)) from exc
    return Principal(
        sub=principal.sub,
        tenant_id=principal.tenant_id,
        typ=principal.typ,
        scopes=principal.scopes,
        agent_id=principal.agent_id,
        agent_version=principal.agent_version,
        obo_sub=principal.obo_sub,
        workspace_id=principal.workspace_id,
    )


def verify_token(token: str, settings: Settings) -> Principal:
    if not settings.jwt_public_key_pem:
        raise UnauthenticatedError("token verification key not configured")
    try:
        claims = jwt.decode(
            token,
            key=settings.jwt_public_key_pem,
            algorithms=ALLOWED_ALGS,
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise UnauthenticatedError(f"invalid token: {exc}") from exc
    tenant_id = claims.get("tenant_id")
    typ = claims.get("typ", "user")
    if not tenant_id or typ not in PRINCIPAL_TYPES:
        raise UnauthenticatedError("token missing tenant_id or has invalid typ")
    return Principal(
        sub=claims["sub"],
        tenant_id=tenant_id,
        typ=typ,
        scopes=list(claims.get("scopes", [])),
        agent_id=claims.get("agent_id"),
        agent_version=claims.get("agent_version"),
        obo_sub=claims.get("obo_sub"),
        workspace_id=claims.get("workspace_id"),
    )


def require_internal(request: Request) -> str:
    """Internal-only dependency (MASTER-FR-014): 403s unless the mesh sidecar
    injected an allowed SPIFFE peer identity. Gates /internal/v1/mcp/invoke, the
    MCP backend facade tool-plane's mcp-gateway federates approved
    write-proposal tool execution to (TPL-FR-012). Mirrors
    pipeline-orchestrator's require_internal exactly."""
    settings: Settings = request.app.state.container.settings
    spiffe = request.headers.get(settings.spiffe_header, "")
    if spiffe not in settings.internal_allowed_spiffe:
        raise PermissionDeniedError("internal endpoint requires an allowed SPIFFE identity")
    return spiffe
