"""AuthN/AuthZ vendored per the wave-1 rule (CONVENTIONS.md).

- Admin plane: RS256 platform JWT in `Authorization: Bearer` (MASTER-FR-010/011).
- Data plane: virtual key in `Authorization: Bearer nk-…` PLUS the platform JWT
  in `X-Windrose-JWT` (AIG-FR-001).
- `alg=none` is impossible by construction — the allowed algorithm list is
  pinned to ["RS256"] (MASTER-FR-014).
- Authorization: scope check behind an AuthzClient port; the OPA sidecar
  adapter (MASTER-FR-012) is a prod stub."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import jwt as pyjwt
from fastapi import Request

from app.config import Settings
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
    cell_cloud: str | None = None
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
    def via_agent(self) -> dict | None:
        if self.typ in ("agent_obo", "agent_autonomous") and self.agent_id:
            return {"agent_id": self.agent_id, "version": self.agent_version}
        return None


class TokenVerifier:
    """RS256 verification against a static PEM (dev/tests) or cached JWKS (prod)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwks: dict[str, object] = {}
        self._jwks_fetched_at = 0.0

    async def _key_for(self, token: str):
        if self.settings.jwt_public_key_pem:
            return self.settings.jwt_public_key_pem
        if not self.settings.jwks_url:
            raise Unauthenticated("no JWT verification key configured")
        header = pyjwt.get_unverified_header(token)
        kid = header.get("kid")
        now = time.monotonic()
        if kid not in self._jwks or now - self._jwks_fetched_at > self.settings.jwks_ttl_seconds:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(self.settings.jwks_url)
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
            agent_version=str(claims["agent_version"]) if claims.get(
                "agent_version") is not None else None,
            obo_sub=claims.get("obo_sub"),
            cell_cloud=claims.get("cell_cloud"),
            workspace_id=claims.get("workspace_id"),
        )


class LocalScopeAuthz:
    """Scope-based allow decision (unit/dev). Actions per MASTER-FR-016."""

    async def allow(self, principal: Principal, action: str,
                    resource_urn: str | None) -> bool:
        return "*" in principal.scopes or action in principal.scopes


class OpaAuthzClient:
    """Real OPA authorization via the shared ``windrose_common`` client: reads
    the per-request permissions projection from Redis and POSTs it as ``input``
    to the OPA data API (``windrose/authz_input``), returning allow/deny
    (MASTER-FR-012). This is the runtime authz client wired by ``main.py`` when
    ``use_real_adapters`` is set; the Redis client and OPA HTTP connection are
    established lazily."""

    def __init__(self, opa_url: str = "http://localhost:8281", *,
                 redis_url: str = "redis://localhost:6379/0"):
        from windrose_common.opaclient import OpaClient
        from windrose_common.redisx import RedisProjection, build_redis

        self._redis = build_redis(redis_url)
        self._client = OpaClient(opa_url, projection=RedisProjection(self._redis))

    async def allow(self, principal: Principal, action: str,
                    resource_urn: str | None) -> bool:
        subject = {
            "id": principal.sub,
            "typ": principal.typ,
            "scopes": principal.scopes,
            "obo_sub": principal.obo_sub or "",
        }
        # Thread the JWT workspace claim so workspace-scoped actions load the
        # per-workspace projection key and satisfy OPA's workspace-context
        # check (mirrors dataset-service).
        return await self._client.allow(
            subject=subject, action=action, tenant=principal.tenant_id,
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


def require(action: str):
    """Route dependency: authenticated principal + authz check for `action`."""

    async def dependency(request: Request) -> Principal:
        principal = get_principal(request)
        authz = request.app.state.authz
        if not await authz.allow(principal, action, None):
            raise PermissionDenied(f"missing permission {action}")
        return principal

    return dependency


def require_operator(action: str):
    """Platform-operator routes additionally require the `ai.platform.admin`
    scope (AIG-FR-070)."""

    async def dependency(request: Request) -> Principal:
        principal = get_principal(request)
        authz = request.app.state.authz
        if not await authz.allow(principal, action, None) or not await authz.allow(
            principal, "ai.platform.admin", None
        ):
            raise PermissionDenied("platform operator permission required")
        return principal

    return dependency


def is_internal(request: Request) -> str | None:
    """Mesh-verified SPIFFE identity, when present and allowed (MASTER-FR-014)."""
    settings: Settings = request.app.state.settings
    spiffe = request.headers.get(settings.spiffe_header, "")
    return spiffe if spiffe in settings.internal_allowed_spiffe else None
