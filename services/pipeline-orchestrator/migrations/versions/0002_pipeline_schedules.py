"""Recurring pipeline schedules (PIPE-FR-050) with RLS (MASTER-FR-001).
Forward-only (MASTER-FR-060).

A schedule fires an existing pipeline template on a cron. The background ticker
scans DUE rows across ALL tenants via a worker session (app.worker=true), exactly
like the transactional-outbox relay in 0001 — so a permissive worker policy is
OR'd onto the tenant-isolation policy. All writes (create run + advance next_fire)
still go through a tenant-scoped session, so the tenant_isolation WITH CHECK
governs them.

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pipeline_schedules (
            schedule_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            template_id uuid NOT NULL,
            name text,
            cron text,
            timezone text NOT NULL DEFAULT 'UTC',
            run_parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
            enabled boolean NOT NULL DEFAULT true,
            next_fire_at timestamptz,
            last_fire_at timestamptz,
            last_run_id uuid,
            created_by text,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE INDEX ix_schedules_due
            ON pipeline_schedules (tenant_id, next_fire_at)
            WHERE enabled;
        """
    )

    # RLS mirrors 0001: ENABLE (policies apply to non-owners) + FORCE (also subject
    # the table owner) + tenant isolation with an explicit WITH CHECK so writes can't
    # escape the tenant. The permissive worker policy is OR'd for the background
    # fire_due scanner, exactly as worker_outbox is for the relay.
    op.execute("ALTER TABLE pipeline_schedules ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE pipeline_schedules FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_pipeline_schedules ON pipeline_schedules
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY worker_pipeline_schedules ON pipeline_schedules
        FOR SELECT
        USING (coalesce(current_setting('app.worker', true), '') = 'true');
        """
    )

    # The runtime role (pipeline_app) is granted DML on tables created by the migrate
    # role via 0001's ALTER DEFAULT PRIVILEGES; grant explicitly too so the runtime
    # role can read/write this table regardless of who owns the migration role.
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON pipeline_schedules TO pipeline_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
