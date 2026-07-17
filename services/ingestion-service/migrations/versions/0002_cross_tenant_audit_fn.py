"""Cross-tenant existence probe for audit (MASTER-FR-003, defect F2).

Under FORCE ROW LEVEL SECURITY the app role cannot see another tenant's row, so
`security.cross_tenant_denied` could never fire from an ordinary SELECT. This
SECURITY DEFINER function (owned by the migration/superuser role) bypasses RLS
to return the owning tenant of an id across the RLS-guarded resource tables,
used solely to decide whether a 404 was a cross-tenant probe.

Revision ID: 0002
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

FN = """
CREATE OR REPLACE FUNCTION ing_owner_tenant(p_table text, p_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_tenant uuid;
BEGIN
    IF p_table NOT IN
        ('connections','ingestions','uploads','schedules','webhook_endpoints') THEN
        RAISE EXCEPTION 'ing_owner_tenant: unsupported table %', p_table;
    END IF;
    EXECUTE format('SELECT tenant_id FROM %I WHERE id = $1', p_table)
        INTO v_tenant USING p_id;
    RETURN v_tenant;
END;
$$;
-- read-only ownership probe, exposed only to the service (never via the API)
GRANT EXECUTE ON FUNCTION ing_owner_tenant(text, uuid) TO PUBLIC;
"""


def upgrade() -> None:
    op.execute(FN)


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
