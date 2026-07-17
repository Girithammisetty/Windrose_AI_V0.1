"""Proposal-execution grant issuance (ART-FR-042; tool-plane TPL-FR-035).

This is the exact token tool-plane's ``authz.ProposalVerifier`` verifies
(services/tool-plane/internal/authz/proposal.go). On human APPROVE, agent-runtime
mints this RS256-signed JWS and the tool call presents it in the MCP
``params._meta.proposal_grant`` field. tool-plane checks: RS256 only, ``exp``
required + short-lived, ``iss`` == GRANT_ISSUER, and binds
``tenant_id``/``tool_id``/``tier``/``args_digest`` to the exact call.

Claims (must match proposal.go ``ProposalGrantClaims`` exactly):
  iss, sub (decider), exp, iat, proposal_id, tenant_id, tool_id, tier, args_digest
"""

from __future__ import annotations

import time

import jwt as pyjwt

from app.constants import GRANT_ISSUER, GRANT_TTL_SECONDS
from app.domain.canonical import args_digest as compute_args_digest
from app.signing.keys import SigningKey


class GrantIssuer:
    def __init__(self, key: SigningKey, *, issuer: str = GRANT_ISSUER,
                 ttl_seconds: int = GRANT_TTL_SECONDS) -> None:
        self._key = key
        self.issuer = issuer
        self.ttl_seconds = ttl_seconds

    def issue(
        self,
        *,
        proposal_id: str,
        tenant_id: str,
        tool_id: str,
        tier: str,
        args: dict,
        decided_by: str,
        now: float | None = None,
    ) -> str:
        """Return the signed grant JWS binding this exact (tenant, tool, tier,
        args) approved by ``decided_by``."""
        iat = int(now if now is not None else time.time())
        claims = {
            "iss": self.issuer,
            "sub": decided_by,
            "iat": iat,
            "exp": iat + self.ttl_seconds,
            "proposal_id": proposal_id,
            "tenant_id": tenant_id,
            "tool_id": tool_id,
            "tier": tier,
            "args_digest": compute_args_digest(args),
        }
        return pyjwt.encode(
            claims, self._key.private_pem, algorithm="RS256",
            headers={"kid": self._key.kid},
        )
