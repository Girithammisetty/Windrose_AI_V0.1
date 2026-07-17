"""Add agent_definitions.owner_tenant: tenant-authored CUSTOM agents (BRD 53).

The 8-9 platform agents are global (owner_tenant NULL). A tenant custom agent
(config over the shared persona_copilot.v1 graph) is owned by, and visible +
runnable only within, its authoring tenant. NULL = platform agent (unchanged
behavior); a set value scopes the definition to one tenant. The catalog list
filters (owner_tenant IS NULL OR owner_tenant = caller-tenant), so a tenant sees
platform agents + its own, never another tenant's.

Forward-only (MASTER-FR-060).

Revision ID: 0009
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS owner_tenant text;")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_def_owner_tenant "
               "ON agent_definitions (owner_tenant);")
