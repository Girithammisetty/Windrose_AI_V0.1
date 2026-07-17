"""End-to-end grant acceptance against the REAL tool-plane mcp-gateway
(verification req 2). agent-runtime issues a signed grant; tool-plane verifies
signature + binding and dispatches to the case-service backend facade; a
forged/absent grant is rejected (PROPOSAL_REQUIRED).

The grant *contract* (issuer, claims, and args_digest byte-compatibility with
tool-plane's Go domain.ArgsDigest) is proven in tests/unit/test_signing.py and,
live, by running tool-plane's REAL authz.ProposalVerifier against this service's
JWKS (see README "Verification"). This test exercises the full /mcp pipeline and
auto-skips unless the operator has seeded a write-proposal tool + backend facade
(set AR_TP_TOOL_ID, AR_TP_VKEY_JWT, AR_TP_TENANT)."""

from __future__ import annotations

import os

import pytest

from app.adapters.tools import ToolPlaneClient
from app.domain.canonical import args_digest  # noqa: F401  (digest is the binding)
from app.signing import GrantIssuer, SigningKey
from tests.integration.conftest import TOOL_PLANE

pytestmark = pytest.mark.integration

TOOL_ID = os.environ.get("AR_TP_TOOL_ID")
AGENT_JWT = os.environ.get("AR_TP_VKEY_JWT")  # agent JWT tool-plane trusts (scope=tool_id)
TENANT = os.environ.get("AR_TP_TENANT")
GRANT_PRIV = os.environ.get("AR_GRANT_PRIVATE_KEY_PEM_PATH", "/tmp/ar_grant_priv.pem")


def _issuer() -> GrantIssuer:
    key = SigningKey(open(GRANT_PRIV).read(), "agent-runtime-2026-1")  # noqa: SIM115
    return GrantIssuer(key)


async def test_signed_grant_accepted_forged_rejected(require_tool_plane):
    if not (TOOL_ID and AGENT_JWT and TENANT):
        pytest.skip("set AR_TP_TOOL_ID / AR_TP_VKEY_JWT / AR_TP_TENANT after seeding "
                    "a write-proposal tool + backend facade in tool-plane")
    client = ToolPlaneClient(TOOL_PLANE)
    args = {"case_id": "c-91", "severity": "high", "assignee_id": "u-dana"}

    # no grant -> PROPOSAL_REQUIRED
    r0 = await client.call(tool_id=TOOL_ID, arguments=args, tenant_id=TENANT,
                           auth_token=AGENT_JWT)
    assert r0.status == "proposal_required"

    # signed grant -> executes
    grant = _issuer().issue(proposal_id="p-itest", tenant_id=TENANT, tool_id=TOOL_ID,
                            tier="write-proposal", args=args, decided_by="u-super")
    r1 = await client.call(tool_id=TOOL_ID, arguments=args, tenant_id=TENANT,
                           auth_token=AGENT_JWT, proposal_grant=grant)
    assert r1.ok and r1.status == "ok"

    # forged grant (wrong key) -> rejected, falls back to PROPOSAL_REQUIRED
    forged = GrantIssuer(SigningKey(None, "agent-runtime-2026-1")).issue(
        proposal_id="p-itest", tenant_id=TENANT, tool_id=TOOL_ID, tier="write-proposal",
        args=args, decided_by="attacker")
    r2 = await client.call(tool_id=TOOL_ID, arguments=args, tenant_id=TENANT,
                           auth_token=AGENT_JWT, proposal_grant=forged)
    assert r2.status == "proposal_required"
