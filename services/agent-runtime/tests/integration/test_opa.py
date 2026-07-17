"""Real OPA authz (ART-FR-044, AC-12): the approver-eligibility check speaks to
the live OPA sidecar. With no matching grant/policy the decision is deny (OPA
fail-closed / deny-by-default), which is exactly the branch that keeps an
unauthorized approver from executing a proposal."""

from __future__ import annotations

import pytest

from app.adapters.authz import OpaAuthz
from tests.conftest import TENANT_A
from tests.integration.conftest import OPA_URL

pytestmark = pytest.mark.integration


async def test_real_opa_deny_by_default(require_opa):
    authz = OpaAuthz(OPA_URL, redis_url="redis://localhost:6379/0",
                     package="windrose/authz_input")
    try:
        allowed = await authz.allow(
            subject={"type": "user", "id": "u-nobody"}, action="proposal.apply",
            tenant=TENANT_A, resource_urn=f"wr:{TENANT_A}:case:case/c-1")
        # real round-trip to OPA; an ungranted subject is denied.
        assert allowed is False
    finally:
        await authz.aclose()
