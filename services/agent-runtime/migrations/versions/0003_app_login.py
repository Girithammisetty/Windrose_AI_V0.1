"""Promote ``agent_runtime_app`` to a non-superuser LOGIN role (tenant-isolation
remediation, cross-tenant RLS-bypass).

0001 already FORCEd row-level security on every tenant table and created
``agent_runtime_app`` as a NOLOGIN group with DML grants. But the shipped runtime
DSN connected as ``windrose``, the dev cluster's SUPERUSER (BYPASSRLS), which
silently bypasses FORCE RLS — so tenant isolation was effectively OFF.

Fix: turn ``agent_runtime_app`` into a LOGIN role (NOSUPERUSER NOBYPASSRLS) and
point the runtime DSN at it. Migrations keep running as the privileged role via
AR_MIGRATE_URL / AR_ADMIN_DATABASE_URL.

Forward-only (MASTER-FR-060).

Revision ID: 0003
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_runtime_app') THEN
                ALTER ROLE agent_runtime_app WITH LOGIN PASSWORD 'agent_runtime_app'
                    NOSUPERUSER NOBYPASSRLS;
            ELSE
                CREATE ROLE agent_runtime_app WITH LOGIN PASSWORD 'agent_runtime_app'
                    NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO agent_runtime_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agent_runtime_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agent_runtime_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agent_runtime_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO agent_runtime_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
