"""Re-remediate RLS policies that regressed off the NULLIF() form (BRD 58 SEC-4).

0005 converged agent-runtime's tenant-isolation policies on the cast-safe
``(NULLIF(current_setting('app.tenant_id', true), ''))::uuid`` form (see its
docstring for why the plain cast throws on a pooled connection once the GUC
reverts to an empty string). Three later migrations added new tenant tables
using the unsafe plain-cast form again, re-introducing exactly the bug 0005
fixed: ``agent_transcripts`` (0006), ``sft_datasets``/``sft_examples`` (0007),
``slm_training_jobs``/``slm_adapters`` (0012). Still fail-closed (a thrown
cast error still denies access), but availability-fragile under the
non-superuser ``agent_runtime_app`` role exactly as 0005 describes.

Pure policy remediation — no application code changes needed.

Forward-only (MASTER-FR-060).

Revision ID: 0018
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_TENANT_TABLES = [
    "agent_transcripts",
    "sft_datasets",
    "sft_examples",
    "slm_training_jobs",
    "slm_adapters",
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


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
