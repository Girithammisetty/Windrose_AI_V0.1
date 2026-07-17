"""FORCE row-level security on every tenant table + ship a non-superuser,
non-owner runtime login role (tenant-isolation remediation, task_971cc66f).

0001 only ENABLEd RLS and created ``dataset_app`` as a NOLOGIN group. ENABLE
(and even FORCE) is silently bypassed for a superuser or the table owner — and
the shipped runtime DSN connected as ``windrose``, the dev cluster's SUPERUSER
(BYPASSRLS). So tenant_isolation_* was effectively OFF: a buggy or compromised
query could read another tenant's datasets.

Fix (both parts required):
  1. FORCE ROW LEVEL SECURITY on every tenant table so the owner is bound too.
  2. Turn ``dataset_app`` into a LOGIN role (NOSUPERUSER NOBYPASSRLS, DML only)
     and point the runtime DSN at it. Migrations keep running as the privileged
     role via DST_MIGRATE_URL; the service logs in as dataset_app, so the
     tenant_isolation policies from 0001 are enforced (MASTER-FR-001).

Forward-only (MASTER-FR-060).

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "datasets",
    "dataset_versions",
    "profiles",
    "lineage_edges",
    "outbox",
    "idempotency_keys",
    "processed_events",
]


def upgrade() -> None:
    # 1. FORCE binds the policy even for the table owner.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

    # 2. Promote dataset_app to a non-privileged LOGIN role (0001 created it
    # NOLOGIN and granted it DML). A dev password ships so the default DSN works
    # out of the box; production overrides DST_DATABASE_URL with Vault creds.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'dataset_app') THEN
                ALTER ROLE dataset_app WITH LOGIN PASSWORD 'dataset_app'
                    NOSUPERUSER NOBYPASSRLS;
            ELSE
                CREATE ROLE dataset_app WITH LOGIN PASSWORD 'dataset_app'
                    NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO dataset_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO dataset_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dataset_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dataset_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO dataset_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
