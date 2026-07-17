"""Add proposals.workspace_id: a real column, separate from args.

Bug found live 2026-07-17 completing the case-triage disposition write-back
loop: ``_authorize_caller``/``_check_eligibility`` resolve workspace-scoped
authz (case.case.update, ai.proposal.approve) from ``args.get("workspace_id")``,
but case.apply_disposition's real published schema is
``additionalProperties: false`` and has no workspace_id field at all — any
value placed in ``args`` for authz purposes would also be sent to tool-plane
and rejected at schema-validation time. workspace_id needs a home that is NOT
the tool-call args. This column is that home; graphs that still carry
workspace_id in args keep working (service.py falls back to args when the
column is null) while triage.py (and future strict-schema tools) use the
column exclusively.

Forward-only (MASTER-FR-060).

Revision ID: 0008
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE proposals ADD COLUMN IF NOT EXISTS workspace_id uuid;")
