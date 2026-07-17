"""Make every tenant-isolation policy cast-safe with NULLIF (RLS remediation).

0001's policies cast ``current_setting('app.tenant_id', true)`` straight to
uuid. After a transaction-local ``set_config(..., true)`` reverts, the GUC
exists as an EMPTY STRING at session level on that pooled connection, so the
next query on the connection makes the policy throw
``invalid input syntax for type uuid: ""`` — permissive policies are all
evaluated, so even the ``worker_outbox`` arm being true doesn't save the
OutboxDispatcher. Latent while the runtime connected as the BYPASSRLS
superuser; bites immediately under the non-superuser ``agent_runtime_app``
role (FORCE RLS). pipeline-orchestrator's policies already use the safe
``NULLIF(current_setting(...), '')::uuid`` form — this migration converges
agent-runtime's seven tenant tables (and 0004's kill_switches policy) on it.

Forward-only (MASTER-FR-060).

Revision ID: 0005
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_TENANT_TABLES = [
    "runs", "sessions", "proposals", "checkpoints", "outbox",
    "tenant_agent_configs",
]


def upgrade() -> None:
    for t in _TENANT_TABLES:
        op.execute(f"""
            DROP POLICY IF EXISTS {t}_isolation ON {t};
            CREATE POLICY {t}_isolation ON {t}
                USING (tenant_id =
                       (NULLIF(current_setting('app.tenant_id', true), ''))::uuid)
                WITH CHECK (tenant_id =
                       (NULLIF(current_setting('app.tenant_id', true), ''))::uuid);
        """)
    op.execute("""
        DROP POLICY IF EXISTS kill_switches_isolation ON kill_switches;
        CREATE POLICY kill_switches_isolation ON kill_switches
            USING (
                tenant_id IS NULL
                OR tenant_id =
                   (NULLIF(current_setting('app.tenant_id', true), ''))::uuid
            )
            WITH CHECK (
                tenant_id IS NULL
                OR tenant_id =
                   (NULLIF(current_setting('app.tenant_id', true), ''))::uuid
            );
    """)


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
