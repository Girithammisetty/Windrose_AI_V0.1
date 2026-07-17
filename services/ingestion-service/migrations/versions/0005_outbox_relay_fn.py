"""Cross-tenant outbox drain functions for the relay worker (MASTER-FR-034).

0004 pointed the runtime DSN at ``ingestion_app`` (NOSUPERUSER NOBYPASSRLS), so
under FORCE ROW LEVEL SECURITY the relay's plain `SELECT ... FROM outbox`
hits the `tenant_isolation` policy's `current_setting('app.tenant_id')` with
no GUC ever set on that connection (the relay drains across all tenants, so
it never calls `tenant_session`) -- Postgres raises
`unrecognized configuration parameter "app.tenant_id"` on every poll and no
event is ever published.

Fix, following the `ing_owner_tenant` precedent (migration 0002): two narrow
SECURITY DEFINER functions, scoped to the outbox table only, so the relay
does not need a broad RLS bypass (which is exactly what 0004 removed for tenant
isolation everywhere else).

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

FN = """
CREATE OR REPLACE FUNCTION ing_outbox_claim_pending(p_limit integer)
RETURNS SETOF outbox
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT * FROM outbox
    WHERE published_at IS NULL
    ORDER BY occurred_at
    LIMIT p_limit
$$;
GRANT EXECUTE ON FUNCTION ing_outbox_claim_pending(integer) TO PUBLIC;

CREATE OR REPLACE FUNCTION ing_outbox_mark_published(p_ids uuid[])
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    UPDATE outbox SET published_at = now() WHERE id = ANY(p_ids)
$$;
GRANT EXECUTE ON FUNCTION ing_outbox_mark_published(uuid[]) TO PUBLIC;
"""


def upgrade() -> None:
    op.execute(FN)


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
