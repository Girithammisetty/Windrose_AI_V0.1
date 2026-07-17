"""FORCE row-level security on every tenant table + ship a non-superuser,
non-owner runtime login role (FINDING-1 remediation).

0001 only ENABLEd RLS. ENABLE leaves table owners and superusers BYPASSING every
policy — so a service connecting as the DB owner/superuser (the old default DSN)
had tenant isolation effectively OFF. FORCE makes RLS apply to the owner too, and
the runtime now connects as the non-privileged ``experiment_app`` login role
(neither owner nor superuser), so tenant_isolation_* policies are enforced for
the shipped default (MASTER-FR-001, AC-12). Forward-only (MASTER-FR-060).

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "experiments", "runs", "run_params", "run_metrics", "run_metric_history",
    "run_tags", "run_artifacts", "run_notes", "registered_models", "model_versions",
    "promotions", "model_registration_log", "model_cards", "mirror_inbox",
    "reconciliation_watermarks", "outbox", "idempotency_keys", "processed_events",
]


def upgrade() -> None:
    # FORCE RLS so the table owner is also subject to the policies. The worker
    # (app.worker=true) and tenant policies from 0001 continue to apply.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

    # Ship a non-superuser, non-owner LOGIN role as the runtime identity. The DML
    # grants come from 0001 (SELECT/INSERT/UPDATE/DELETE only — no ownership). A
    # dev password is set here so the default DSN works out of the box; production
    # overrides EXP_DATABASE_URL with mesh/Vault-issued credentials.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'experiment_app') THEN
                ALTER ROLE experiment_app WITH LOGIN PASSWORD 'experiment_app';
            ELSE
                CREATE ROLE experiment_app LOGIN PASSWORD 'experiment_app';
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO experiment_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO experiment_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO experiment_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
