"""runs.final_text + outbox worker RLS policy.

* ``runs.final_text`` persists the final assistant answer on completion so
  non-streaming clients can read it back from GET /api/v1/runs/{id} (the
  Temporal workflow previously discarded the computed answer entirely).
* ``worker_outbox`` is the permissive RLS policy the outbox relay runs under
  (``app.worker`` GUC, mirroring pipeline-orchestrator) so it can drain
  unpublished rows across ALL tenants without a tenant GUC.

Forward-only (MASTER-FR-060).

Revision ID: 0002
"""
# ruff: noqa: E501

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS final_text text;")
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_policies
                WHERE tablename = 'outbox' AND policyname = 'worker_outbox'
            ) THEN
                CREATE POLICY worker_outbox ON outbox
                    USING (coalesce(current_setting('app.worker', true), '') = 'true');
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS worker_outbox ON outbox;")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS final_text;")
