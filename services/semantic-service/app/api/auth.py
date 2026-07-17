"""AuthN/AuthZ vendored per the wave-1 rule (CONVENTIONS.md).

- External requests: RS256 JWT (MASTER-FR-010/011). `alg=none` is impossible by
  construction — the allowed algorithm list is pinned to ["RS256"].
- Internal requests: SPIFFE identity header injected by the mesh sidecar after
  mTLS termination (MASTER-FR-014).
- Authorization: local scope check behind an AuthzClient port; the OPA sidecar
  adapter (MASTER-FR-012) is stubbed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import jwt as pyjwt
from fastapi import Request

from app.config import Settings
from app.domain.errors import PermissionDenied, Unauthenticated
from app.domain.services import CallCtx


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
        """The identity whose permission projection governs this request. For an
        agent_obo token the agent acts WITH THE USER'S permissions (intersection
        of user perms + agent toolset, §8.9), so authz is keyed on obo_sub — NOT
        the agent principal (which carries no content grants). Mirrors
        experiment-service / memory-service (the services that resolve OBO
        correctly); using ``sub`` here made every agent_obo semantic call 403."""
        if self.typ == "agent_obo" and self.obo_sub:
            return self.obo_sub
        return self.sub

    @property
    def via_agent(self) -> dict | None:
        if self.typ in ("agent_obo", "agent_autonomous") and self.agent_id:
            return {"agent_id": self.agent_id, "version": self.agent_version}
        return None

    @property
    def is_agent(self) -> bool:
        return self.typ in ("agent_obo", "agent_autonomous")

    def ctx(self, trace_id: str | None = None) -> CallCtx:
        return CallCtx(
            tenant_id=self.tenant_id,
            actor=self.actor,
            via_agent=self.via_agent,
            trace_id=trace_id,
            subject=self.sub,
            is_agent=self.is_agent,
        )


class TokenVerifier:
    """RS256 verification against a static PEM (dev/tests) or cached JWKS (prod)."""

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
                for k in resp.json().get("keys", [])
                if k.get("kty") == "RSA"
            }
            self._jwks_fetched_at = now
        if kid not in self._jwks:
            raise Unauthenticated("unknown signing key")
        return self._jwks[kid]

    async def verify(self, token: str) -> Principal:
        try:
            key = await self._key_for(token)
            claims = pyjwt.decode(
                token,
                key,
                algorithms=["RS256"],  # alg=none / HS* rejected by construction
                audience=self.settings.jwt_audience,
                issuer=self.settings.jwt_issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Unauthenticated:
            raise
        except Exception as exc:  # noqa: BLE001 - any JWT failure is a 401
            raise Unauthenticated(f"invalid token: {exc}") from exc
        if not claims.get("tenant_id"):
            raise Unauthenticated("token missing tenant_id claim")
        scopes = claims.get("scopes") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        return Principal(
            sub=claims["sub"],
            tenant_id=claims["tenant_id"],
            typ=claims.get("typ", "user"),
            scopes=list(scopes),
            agent_id=claims.get("agent_id"),
            agent_version=claims.get("agent_version"),
            obo_sub=claims.get("obo_sub"),
            workspace_id=claims.get("workspace_id"),
        )


class LocalScopeAuthz:
    """Scope-based allow decision (unit/dev double). Actions per MASTER-FR-016.
    NOT wired into the real runtime container (OpaAuthzClient is)."""

    async def allow(self, principal: Principal, action: str, resource_urn: str | None) -> bool:
        return "*" in principal.scopes or action in principal.scopes


class OpaAuthzClient:
    """Real OPA authorization via the shared ``windrose_common`` client: reads the
    per-request permissions projection from Redis (written by rbac-service's CDC
    projector) and POSTs it as ``input`` to the OPA data API
    (``windrose.authz_input``), returning allow/deny (MASTER-FR-012). Runtime
    authz client."""

    def __init__(
        self,
        opa_url: str = "http://localhost:8281",
        *,
        redis_url: str = "redis://localhost:6379/0",
    ):
        from windrose_common.opaclient import OpaClient
        from windrose_common.redisx import RedisProjection, build_redis

        self._redis = build_redis(redis_url)
        self._client = OpaClient(opa_url, projection=RedisProjection(self._redis))

    async def allow(self, principal: Principal, action: str, resource_urn: str | None) -> bool:
        subject = {
            "id": principal.effective_user,
            "typ": principal.typ,
            "scopes": principal.scopes,
            "obo_sub": principal.obo_sub or "",
        }
        # Thread the JWT workspace claim so workspace-scoped actions (all
        # semantic.* actions) load the per-workspace projection key and satisfy
        # OPA's workspace-context check (mirrors dataset-service).
        return await self._client.allow(
            subject=subject,
            action=action,
            tenant=principal.tenant_id,
            resource_urn=resource_urn,
            workspace_id=getattr(principal, "workspace_id", None),
        )

    async def aclose(self) -> None:
        await self._redis.aclose()


def get_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise Unauthenticated("missing bearer token")
    return principal


def get_bearer_token(request: Request) -> str | None:
    """The caller's raw bearer JWT, for forwarding to sibling services that
    authorize as the calling user (e.g. query-service's dry-run — it has no
    internal/SPIFFE route, see HttpQueryServiceClient)."""
    return getattr(request.state, "bearer_token", None)


def require(action: str):
    """Route dependency: authenticated principal + authz check for `action`."""

    async def dependency(request: Request) -> Principal:
        principal = get_principal(request)
        authz = request.app.state.authz
        if not await authz.allow(principal, action, None):
            raise PermissionDenied(f"missing permission {action}")
        return principal

    return dependency


def require_internal(request: Request) -> str:
    """Internal mTLS guard: mesh-verified SPIFFE identity allowlist (MASTER-FR-014)."""
    settings: Settings = request.app.state.settings
    spiffe = request.headers.get(settings.spiffe_header, "")
    if spiffe not in settings.internal_allowed_spiffe:
        raise PermissionDenied("internal endpoint requires an allowed SPIFFE identity")
    return spiffe
