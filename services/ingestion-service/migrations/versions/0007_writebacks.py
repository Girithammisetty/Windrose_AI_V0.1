"""Decision write-back jobs (INS-FR-061 SoR write adapters).

The governed outbound counterpart of ingestion: a `writebacks` job records a
platform decision (e.g. a case disposition) destined for a tenant's system of
record via an `outgoing` connection. Delivery is proposal-mode (four-eyes) and
idempotent per (tenant, connection, idempotency_key). Tenant-isolated by RLS
like every other table (MASTER-FR-001).

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE writebacks (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    decision_kind text NOT NULL,
    decision_ref text NOT NULL,
    idempotency_key text NOT NULL,
    target jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'pending_approval',
    approval_mode text NOT NULL DEFAULT 'four_eyes',
    requested_by text,
    approved_by text,
    attempts integer NOT NULL DEFAULT 0,
    last_error text,
    target_ref text,
    delivered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT writebacks_status_check CHECK (status IN
        ('pending_approval','approved','delivering','delivered','failed','rejected')),
    CONSTRAINT writebacks_approval_mode_check CHECK (approval_mode IN ('four_eyes','auto')),
    CONSTRAINT uq_writebacks_tenant_conn_idem UNIQUE (tenant_id, connection_id, idempotency_key)
);
CREATE INDEX ix_writebacks_tenant_status ON writebacks (tenant_id, status);
CREATE INDEX ix_writebacks_decision_ref ON writebacks (tenant_id, decision_ref);
"""


def upgrade() -> None:
    op.execute(DDL)
    op.execute("ALTER TABLE writebacks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE writebacks FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON writebacks "
        "USING (tenant_id = current_setting('app.tenant_id')::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid)"
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
