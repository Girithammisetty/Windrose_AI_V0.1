"""AuthN/AuthZ (MASTER-FR-010/011/012/014).

- External requests: RS256 JWT (alg=none impossible by construction).
- Internal requests: SPIFFE identity header injected by the mesh sidecar after mTLS.
- Authorization: real OPA (windrose_common) reading the Redis permissions projection;
  a local scope check backs the unit/dev tier.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import jwt as pyjwt
from fastapi import Request

from app.config import Settings
from app.domain.entities import CallCtx
from app.domain.errors import PermissionDenied, Unauthenticated


@dataclass(slots=True)
class Principal:
    sub: str
    tenant_id: str
    typ: str = "user"
    scopes: list[str] = field(default_factory=list)
    agent_id: str | None = None
    agent_version: str | None = None
    obo_sub: str | None = None
    workspace_id: str | None = None

    @property
    def actor(self) -> dict:
        if self.typ == "agent_autonomous":
            return {"type": "agent", "id": self.agent_id or self.sub}
        if self.typ == "agent_obo":
            return {"type": "user", "id": self.obo_sub or self.sub}
        if self.typ == "service":
            return {"type": "service", "id": self.sub}
        return {"type": "user", "id": self.sub}

    @property
    def effective_user(self) -> str:
        """Identity whose permission projection governs this request. An agent_obo
        token acts WITH THE USER'S permissions (§8.9), so authz keys on obo_sub,
        not the agent principal. Mirrors experiment/memory; using ``sub`` 403'd
        agent_obo grounding calls (e.g. the model-training agent)."""
        if self.typ == "agent_obo" and self.obo_sub:
            return self.obo_sub
        return self.sub

    @property
    def via_agent(self) -> dict | None:
        if self.typ in ("agent_obo", "agent_autonomous") and self.agent_id:
            return {"agent_id": self.agent_id, "version": self.agent_version}
        return None

    def ctx(self, trace_id: str | None = None) -> CallCtx:
        return CallCtx(tenant_id=self.tenant_id, actor=self.actor,
                       via_agent=self.via_agent, workspace_id=self.workspace_id,
                       subject_id=self.sub, trace_id=trace_id)


class TokenVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwks: dict[str, object] = {}
        self._jwks_fetched_at = 0.0
        # Reuse one client for JWKS refreshes rather than a fresh handshake each.
        self._jwks_client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._jwks_client is None:
            self._jwks_client = httpx.AsyncClient(timeout=5)
        return self._jwks_client

    async def _key_for(self, token: str):
        if self.settings.jwt_public_key_pem:
            return self.settings.jwt_public_key_pem
        if not self.settings.jwks_url:
            raise Unauthenticated("no JWT verification key configured")
        header = pyjwt.get_unverified_header(token)
        kid = header.get("kid")
        now = time.monotonic()
        if kid not in self._jwks or now - self._jwks_fetched_at > self.settings.jwks_ttl_seconds:
            resp = await self._http().get(self.settings.jwks_url)
            resp.raise_for_status()
            self._jwks = {
                k["kid"]: pyjwt.algorithms.RSAAlgorithm.from_jwk(k)
                for k in resp.json().get("keys", []) if k.get("kty") == "RSA"}
            self._jwks_fetched_at = now
        if kid not in self._jwks:
            raise Unauthenticated("unknown signing key")
        return self._jwks[kid]

    async def verify(self, token: str) -> Principal:
        try:
            key = await self._key_for(token)
            claims = pyjwt.decode(
                token, key, algorithms=["RS256"], audience=self.settings.jwt_audience,
                issuer=self.settings.jwt_issuer,
                options={"require": ["exp", "iss", "aud", "sub"]})
        except Unauthenticated:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Unauthenticated(f"invalid token: {exc}") from exc
        if not claims.get("tenant_id"):
            raise Unauthenticated("token missing tenant_id claim")
        scopes = claims.get("scopes") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        return Principal(
            sub=claims["sub"], tenant_id=claims["tenant_id"],
            typ=claims.get("typ", "user"), scopes=list(scopes),
            agent_id=claims.get("agent_id"), agent_version=claims.get("agent_version"),
            obo_sub=claims.get("obo_sub"), workspace_id=claims.get("workspace_id"))


class LocalScopeAuthz:
    """Scope-based allow decision (unit/dev)."""

    async def allow(self, principal: Principal, action: str, resource_urn):
        return "*" in principal.scopes or action in principal.scopes


class OpaAuthzClient:
    """Real OPA authorization via windrose_common (MASTER-FR-012)."""

    def __init__(self, opa_url="http://localhost:8281", *,
                 redis_url="redis://localhost:6379/0"):
        from windrose_common.opaclient import OpaClient
        from windrose_common.redisx import build_redis

        self._redis = build_redis(redis_url)
        self._client = OpaClient(opa_url)

    async def allow(self, principal: Principal, action: str, resource_urn):
        from windrose_common.projection import load_projection

        subject = {"id": principal.effective_user, "typ": principal.typ, "scopes": principal.scopes,
                   "obo_sub": principal.obo_sub or ""}
        proj = await load_projection(
            self._redis, tenant=principal.tenant_id, subject=subject, action=action,
            workspace_id=principal.workspace_id, resource_urn=resource_urn)
        return await self._client.allow(
            subject=subject, action=action, tenant=principal.tenant_id,
            resource_urn=resource_urn, workspace_id=principal.workspace_id,
            projection=proj)


def get_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise Unauthenticated("missing bearer token")
    return principal


def require(action: str):
    async def dependency(request: Request) -> Principal:
        principal = get_principal(request)
        authz = request.app.state.authz
        if not await authz.allow(principal, action, None):
            raise PermissionDenied(f"missing permission {action}")
        return principal

    return dependency


def require_internal(request: Request) -> str:
    settings: Settings = request.app.state.settings
    spiffe = request.headers.get(settings.spiffe_header, "")
    if spiffe not in settings.internal_allowed_spiffe:
        raise PermissionDenied("internal endpoint requires an allowed SPIFFE identity")
    return spiffe
