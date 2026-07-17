"""Ship a non-superuser LOGIN app role ``ingestion_app`` (tenant-isolation
remediation, cross-tenant RLS-bypass).

0001 already ENABLEd + FORCEd row-level security on every tenant table, but the
shipped runtime DSN connected as ``windrose``, the dev cluster's SUPERUSER
(BYPASSRLS), which silently bypasses FORCE RLS — so tenant isolation was
effectively OFF: a buggy or compromised query could read another tenant's
connections/ingestions/uploads.

Fix: create ``ingestion_app`` (NOSUPERUSER NOBYPASSRLS, DML only) and point the
runtime DSN at it. Migrations keep running as the privileged role via
INGESTION_MIGRATE_URL (see migrations/env.py).

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
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ingestion_app') THEN
                ALTER ROLE ingestion_app WITH LOGIN PASSWORD 'ingestion_app'
                    NOSUPERUSER NOBYPASSRLS;
            ELSE
                CREATE ROLE ingestion_app WITH LOGIN PASSWORD 'ingestion_app'
                    NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO ingestion_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ingestion_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ingestion_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ingestion_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO ingestion_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
