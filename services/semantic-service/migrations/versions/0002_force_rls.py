"""FORCE row-level security on every tenant table (tenant-isolation hardening).

0001 only ENABLEd RLS on the per-tenant tables and created the ``tenant_isolation``
policies. ENABLE is silently bypassed for a superuser or the table OWNER, so
isolation held only by virtue of the app connecting as a non-owning role — one
ownership or migration change away from silently disabling tenant isolation.
FORCE ROW LEVEL SECURITY makes the policies apply even to the table owner,
matching every other service in the repo.

Forward-only is the service default (MASTER-FR-060), but FORCE ↔ NO FORCE is a
purely reversible attribute flip, so a real downgrade is provided.

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Every table that 0001 ENABLEd RLS on with a ``tenant_isolation_*`` policy.
TENANT_TABLES = [
    "semantic_models",
    "model_versions",
    "entities",
    "dimensions",
    "measures",
    "join_paths",
    "verified_queries",
    "compile_log",
    "operations",
    "chart_refs",
    "outbox",
    "idempotency_keys",
    "processed_events",
]


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
