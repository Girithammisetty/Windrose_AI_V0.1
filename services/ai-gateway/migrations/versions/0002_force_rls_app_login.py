"""FORCE row-level security on every tenant table + promote ``ai_gateway_app``
to a non-superuser LOGIN role (tenant-isolation remediation, cross-tenant
RLS-bypass).

0001 only ENABLEd RLS and created ``ai_gateway_app`` as a NOLOGIN group. ENABLE
(and even FORCE) is silently bypassed for a superuser or the table owner — and
the shipped runtime DSN connected as ``windrose``, the dev cluster's SUPERUSER
(BYPASSRLS). So tenant_isolation_* was effectively OFF: a buggy or compromised
query could read another tenant's rows.

Fix (both parts required):
  1. FORCE ROW LEVEL SECURITY on every RLS table so the owner is bound too.
  2. Turn ``ai_gateway_app`` into a LOGIN role (NOSUPERUSER NOBYPASSRLS, DML
     only) and point the runtime DSN at it. Migrations keep running as the
     privileged role via AIG_MIGRATE_URL.

Forward-only (MASTER-FR-060).

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Every table that had RLS ENABLEd in 0001 (tenant-scoped, tenant_configs and
# the worker-scoped budget tables). FORCE binds the policy even for the owner.
FORCE_TABLES = [
    "provider_deployments",
    "model_ladders",
    "budgets",
    "virtual_keys",
    "guardrail_policies",
    "semantic_cache_entries",
    "request_log",
    "outbox",
    "idempotency_keys",
    "processed_events",
    "tenant_configs",
    "budget_spend",
    "budget_reservations",
    "budget_threshold_flags",
]


def upgrade() -> None:
    for table in FORCE_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_gateway_app') THEN
                ALTER ROLE ai_gateway_app WITH LOGIN PASSWORD 'ai_gateway_app'
                    NOSUPERUSER NOBYPASSRLS;
            ELSE
                CREATE ROLE ai_gateway_app WITH LOGIN PASSWORD 'ai_gateway_app'
                    NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO ai_gateway_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ai_gateway_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ai_gateway_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_gateway_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO ai_gateway_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
