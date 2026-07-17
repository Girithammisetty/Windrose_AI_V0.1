"""Agent / OBO token minting for downstream calls (ART-FR-012, BR-6).

Tool calls to tool-plane carry either the run's OBO token (user-initiated) or the
agent-principal token (autonomous). case-service applies a proposal only under an
``agent_obo`` token whose {obo_sub, agent_id, agent_version} produce the
MASTER-FR-041 dual-attribution actor.

In prod these are minted/exchanged via identity-service. In dev/tests agent-runtime
self-signs them with its RS256 signing key (the same key it publishes at its JWKS
endpoint), so tool-plane / case-service — configured to trust that JWKS/issuer —
verify them for real. This is a real RS256 token, not a stub; only the *issuer of
record* differs between dev (self) and prod (identity-service).
"""

from __future__ import annotations

import time

import jwt as pyjwt

from app.signing.keys import SigningKey


class TokenMinter:
    def __init__(
        self,
        key: SigningKey,
        *,
        issuer: str,
        audience: str,
        ttl_seconds: int = 900,
    ) -> None:
        self._key = key
        self.issuer = issuer
        self.audience = audience
        self.ttl_seconds = ttl_seconds

    def _base(self, sub: str, tenant_id: str) -> dict:
        iat = int(time.time())
        return {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": sub,
            "iat": iat,
            "exp": iat + self.ttl_seconds,
            "tenant_id": tenant_id,
        }

    def mint_agent_obo(
        self,
        *,
        tenant_id: str,
        obo_sub: str,
        agent_key: str,
        agent_version: int,
        workspace_id: str | None,
        scopes: list[str],
    ) -> str:
        """agent_obo token: acts for the user (obo_sub) via the agent."""
        claims = self._base(f"agent:{agent_key}@v{agent_version}", tenant_id)
        claims.update(
            typ="agent_obo",
            obo_sub=obo_sub,
            agent_id=agent_key,
            agent_version=str(agent_version),
            scopes=scopes,
        )
        if workspace_id:
            claims["workspace_id"] = workspace_id
        return pyjwt.encode(claims, self._key.private_pem, algorithm="RS256",
                            headers={"kid": self._key.kid})

    def mint_agent_autonomous(
        self,
        *,
        tenant_id: str,
        agent_key: str,
        agent_version: int,
        scopes: list[str],
    ) -> str:
        """agent_autonomous token: the agent's own principal (governance runs)."""
        claims = self._base(f"agent:{agent_key}@v{agent_version}", tenant_id)
        claims.update(
            typ="agent_autonomous",
            agent_id=agent_key,
            agent_version=str(agent_version),
            scopes=scopes,
        )
        return pyjwt.encode(claims, self._key.private_pem, algorithm="RS256",
                            headers={"kid": self._key.kid})

    def mint_service(self, *, tenant_id: str, scopes: list[str]) -> str:
        """service token: agent-runtime acting as itself (e.g. realtime-hub
        internal publish, which requires typ=service|agent* + a scope such as
        ``realtime.publish`` — see realtime-hub authenticatePublisher)."""
        claims = self._base("svc:agent-runtime", tenant_id)
        claims.update(typ="service", scopes=scopes)
        return pyjwt.encode(claims, self._key.private_pem, algorithm="RS256",
                            headers={"kid": self._key.kid})
