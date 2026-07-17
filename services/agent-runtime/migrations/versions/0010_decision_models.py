"""Decision models (BRD 54) — tenant-authored, versioned decision tables.

A decision model is an ordered set of condition->outcome rules over a dataset's
real columns that executes to the SAME governed four-eyes proposal an agent
produces (reusing ProposalService). Tenant-scoped (RLS via tenant_id); the rules
+ default outcome live in jsonb. Forward-only (MASTER-FR-060).

Revision ID: 0010
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_models (
            id             uuid PRIMARY KEY,
            tenant_id      text NOT NULL,
            workspace_id   text,
            name           text NOT NULL,
            dataset_urn    text,
            version        int NOT NULL DEFAULT 1,
            status         text NOT NULL DEFAULT 'draft'
                           CHECK (status IN ('draft','published')),
            rules          jsonb NOT NULL DEFAULT '[]',
            default_outcome jsonb,
            created_by     text,
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_decision_models_tenant "
               "ON decision_models (tenant_id, name);")
