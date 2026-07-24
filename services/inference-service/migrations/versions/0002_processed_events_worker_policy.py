"""B7 (BRD 58): cross-tenant worker policy for processed_events, mirroring
0001's `worker_outbox` policy on `outbox`.

`processed_events` was created as a plain per-tenant TENANT_TABLES member (0001)
with only `tenant_isolation_processed_events` (tenant_id = the session GUC). A
background retention sweep runs with NO tenant context (it prunes across every
tenant), so under FORCE ROW LEVEL SECURITY the existing policy alone blocks
every row -- not an error, just a silent no-op, exactly like `outbox` would be
without its `worker_outbox` policy. Same fix, same table shape, same pattern as
dataset-service 0005 / memory-service 0003 / the other B7 owners.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE POLICY worker_processed_events ON processed_events
        USING (coalesce(current_setting('app.worker', true), '') = 'true');
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
