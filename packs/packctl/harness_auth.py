"""Local token provider for packctl: mints REAL RS256 JWTs with the e2e
harness IdP key (the same signing key every running service's JWKS verifies
against — deploy/e2e). In a production deployment the operator would supply
tokens from the tenant's real IdP instead; this module is the local-stack
credential source, not a bypass: every request is verified by the platform's
own JWT middleware + OPA authorization like any user call.

Subjects mirror seed_platform's real personas: the AUTHOR is the triage
manager (a real member of the tenant's Admin permission group, so the rbac
projection authorizes writes truthfully) and the APPROVER is the distinct
approver subject (four-eyes: semantic-service rejects self-approval).
"""

from __future__ import annotations

import os
import sys
import uuid

_E2E = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "deploy", "e2e")
sys.path.insert(0, _E2E)
sys.path.insert(0, os.path.join(_E2E, "lib"))

import common as _c  # noqa: E402  (harness IdP: real signed JWTs)


def load_context() -> dict:
    """Run the idempotent platform boot seed (tenant alignment, the operators'
    REAL Admin membership, rbac perm:* + authz:proj:* projections — the same
    real grant path seed_claims_demo runs first), then return the ALIGNED
    tenant/workspace and operator subjects from the seed modules. The
    workspace MUST come from the post-alignment module globals: seed_platform
    re-points it at rbac's real default workspace, and every workspace-scoped
    authorization resolves against that one."""
    local_dir = os.path.join(os.path.dirname(_E2E), "local")
    sys.path.insert(0, local_dir)
    import driver as d  # noqa: PLC0415
    import seed_platform as sp  # noqa: PLC0415
    sp.ensure_platform_seeded()
    return {"tenant": d.TENANT, "workspace": d.WORKSPACE,
            "manager": d.MANAGER, "approver": d.APPROVER}


def token_providers(ctx: dict):
    """(author_token, approver_token, agent_token) callables. Tokens are
    short-lived and re-minted per call."""
    tenant, ws = ctx["tenant"], ctx["workspace"]
    session = str(uuid.uuid4())

    def author() -> str:
        return _c.user_token(ctx["manager"], tenant, ["*"], workspace_id=ws)

    def approver() -> str:
        return _c.user_token(ctx["approver"], tenant, ["*"], workspace_id=ws)

    def agent() -> str:
        return _c.agent_obo_token(ctx["manager"], tenant, ["*"], session,
                                  workspace_id=ws)

    return author, approver, agent
