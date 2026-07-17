"""Real JWKS-backed JWT verification (MASTER-FR-010/011/014).

* ``JwksCache`` fetches the identity-service JWKS over HTTP and caches keys by
  ``kid`` with a TTL refresh (default 5 min). A ``kid`` miss forces one refresh.
* ``JwtVerifier`` verifies RS256 tokens: ``alg=none`` (and every non-RS256 alg)
  is rejected *before* decode by inspecting the header and by pinning
  ``algorithms=["RS256"]``; ``exp``/``iss``/``aud``/``sub`` are required and
  validated. It accepts either a static PEM (dev/tests) or a JWKS URL (prod).
* ``Principal`` maps the verified claims to Windrose's dual-attribution actor
  model (MASTER-FR-041 / §2.2-011).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import jwt as pyjwt

ALLOWED_ALGS = ["RS256"]
PRINCIPAL_TYPES = {"user", "service", "agent_obo", "agent_autonomous"}


class InvalidTokenError(Exception):
    """Raised for any authentication failure (maps to HTTP 401)."""


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
    def actor(self) -> dict[str, str]:
        if self.typ == "agent_autonomous":
            return {"type": "agent", "id": self.agent_id or self.sub}
        if self.typ == "agent_obo":
            return {"type": "user", "id": self.obo_sub or self.sub}
        return {"type": self.typ, "id": self.sub}

    @property
    def via_agent(self) -> dict[str, str] | None:
        if self.typ.startswith("agent") and self.agent_id:
            return {"agent_id": self.agent_id, "version": self.agent_version or "unknown"}
        return None


class JwksCache:
    def __init__(self, jwks_url: str, *, ttl_seconds: int = 300) -> None:
        self.jwks_url = jwks_url
        self.ttl_seconds = ttl_seconds
        self._keys: dict[str, object] = {}
        self._fetched_at = 0.0

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(self.jwks_url)
            resp.raise_for_status()
        self._keys = {
            k["kid"]: pyjwt.algorithms.RSAAlgorithm.from_jwk(k)
            for k in resp.json().get("keys", [])
            if k.get("kty") == "RSA"
        }
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str | None):
        stale = time.monotonic() - self._fetched_at > self.ttl_seconds
        if kid not in self._keys or stale:
            await self._refresh()
        if kid not in self._keys:
            raise InvalidTokenError("unknown signing key (kid)")
        return self._keys[kid]


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key_pem: str | None = None,
        jwks: JwksCache | None = None,
    ) -> None:
        if not public_key_pem and jwks is None:
            raise ValueError("JwtVerifier needs either public_key_pem or a JwksCache")
        self.issuer = issuer
        self.audience = audience
        self.public_key_pem = public_key_pem
        self.jwks = jwks

    async def verify(self, token: str) -> Principal:
        try:
            header = pyjwt.get_unverified_header(token)
        except pyjwt.PyJWTError as exc:
            raise InvalidTokenError(f"malformed token header: {exc}") from exc
        # MASTER-FR-014: reject alg=none / non-RS256 up front (belt-and-braces with
        # the pinned algorithms list below).
        if header.get("alg") not in ALLOWED_ALGS:
            raise InvalidTokenError(f"forbidden alg: {header.get('alg')!r}")
        key = self.public_key_pem
        if key is None:
            key = await self.jwks.get_key(header.get("kid"))
        try:
            claims = pyjwt.decode(
                token,
                key=key,
                algorithms=ALLOWED_ALGS,
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except pyjwt.PyJWTError as exc:
            raise InvalidTokenError(f"invalid token: {exc}") from exc
        tenant_id = claims.get("tenant_id")
        typ = claims.get("typ", "user")
        if not tenant_id or typ not in PRINCIPAL_TYPES:
            raise InvalidTokenError("token missing tenant_id or has invalid typ")
        scopes = claims.get("scopes") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        return Principal(
            sub=claims["sub"],
            tenant_id=tenant_id,
            typ=typ,
            scopes=list(scopes),
            agent_id=claims.get("agent_id"),
            agent_version=claims.get("agent_version"),
            obo_sub=claims.get("obo_sub"),
            workspace_id=claims.get("workspace_id"),
        )
