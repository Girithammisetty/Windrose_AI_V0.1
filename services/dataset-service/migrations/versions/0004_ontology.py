"""pack-service inc11 — governed domain ONTOLOGY (entity-type registry).

A capability pack declares the entity TYPES its vertical operates on (Vendor,
Invoice, PaymentRun, ...) with their attributes + typed RELATIONSHIPS to other
types (Vendor has_many Invoice; Invoice references PaymentRun) — the type-level
domain model the flat dataset-derived semantic entities cannot express, and
distinct from entity RESOLUTION (0003, which resolves instances of these types
into clusters). Tenant-RLS like every other table (0001/0002); dataset_app
already holds DML via 0002 default privileges. Forward-only (MASTER-FR-060).

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
        CREATE TABLE ontology_entities (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            entity_key text NOT NULL,
            name text NOT NULL,
            description text NOT NULL DEFAULT '',
            attributes jsonb NOT NULL DEFAULT '[]'::jsonb,
            relationships jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT ux_ontology_entity UNIQUE (tenant_id, workspace_id, entity_key)
        );
        CREATE INDEX ix_ontology_ws ON ontology_entities (tenant_id, workspace_id);
        """
    )
    op.execute("ALTER TABLE ontology_entities ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE ontology_entities FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_ontology_entities ON ontology_entities
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
