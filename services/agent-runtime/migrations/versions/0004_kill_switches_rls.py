"""Enable tenant-isolation RLS on ``kill_switches`` (residual of the cross-tenant
RLS-bypass remediation).

0001 FORCEd row-level security on the run-scoped tenant tables (runs, sessions,
proposals, checkpoints, outbox) but ``kill_switches`` was left with RLS OFF even
though it carries a (nullable) ``tenant_id`` — so any session could read every
tenant's kill-switch config. Runtime kill *enforcement* is Redis-backed
(adapters/killswitch.py), so this table is the durable record read by the
registry API; adding RLS here closes a cross-tenant metadata read without
touching the hot path.

``tenant_id`` is nullable to hold PLATFORM-GLOBAL kills (agent-wide / version-wide
with no tenant). The policy therefore keeps NULL-tenant (global) rows visible to
every tenant session while isolating tenant-specific rows. Who may *create* a kill
is already gated by the registry API authz (tool.kill.create); RLS here is
read-isolation defense-in-depth. FORCE so the owner is subject to the policy too.

Forward-only (MASTER-FR-060).

Revision ID: 0004
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE kill_switches ENABLE ROW LEVEL SECURITY;
        ALTER TABLE kill_switches FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS kill_switches_isolation ON kill_switches;
        CREATE POLICY kill_switches_isolation ON kill_switches
            USING (
                tenant_id IS NULL
                OR tenant_id = (current_setting('app.tenant_id', true))::uuid
            )
            WITH CHECK (
                tenant_id IS NULL
                OR tenant_id = (current_setting('app.tenant_id', true))::uuid
            );
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
