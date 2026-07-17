"""SLM distillation — milestone 2: versioned SFT dataset corpus (curation output).

Curation reads the ``agent_transcripts`` corpus (milestone 1) and emits a
GOVERNED, versioned SFT dataset: a frozen snapshot of chat-format training
examples derived from consented, human-approved/edited runs (an edited proposal
is the gold input->corrected-output pair). Two tables:

- ``sft_datasets``  — one row per built version (agent_key + version), with the
  curation params, counts, a content checksum, and consent-verified flag. A
  built dataset is immutable (a snapshot); re-curation mints a new version.
- ``sft_examples``  — the frozen chat-format rows, with a lineage pointer back
  to the source transcript.

Both RLS-isolated + granted to agent_runtime_app. Forward-only (MASTER-FR-060).
Revision ID: 0007
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sft_datasets (
            dataset_id      uuid PRIMARY KEY,
            tenant_id       uuid NOT NULL,
            agent_key       text NOT NULL,
            version         int NOT NULL,
            status          text NOT NULL DEFAULT 'built',
            row_count       int NOT NULL DEFAULT 0,
            source_count    int NOT NULL DEFAULT 0,
            curation_params jsonb NOT NULL DEFAULT '{}',
            checksum        text NOT NULL DEFAULT '',
            consent_verified boolean NOT NULL DEFAULT true,
            created_by      text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, agent_key, version)
        );
        CREATE INDEX ix_sft_datasets_agent ON sft_datasets (tenant_id, agent_key, created_at DESC);

        CREATE TABLE sft_examples (
            dataset_id          uuid NOT NULL,
            tenant_id           uuid NOT NULL,
            ord                 int NOT NULL,
            messages            jsonb NOT NULL,
            target_kind         text NOT NULL,
            source_transcript_id uuid,
            example_hash        text NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (dataset_id, ord)
        );
        CREATE INDEX ix_sft_examples_dataset ON sft_examples (tenant_id, dataset_id, ord);

        GRANT SELECT, INSERT, UPDATE, DELETE ON sft_datasets TO agent_runtime_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON sft_examples TO agent_runtime_app;

        ALTER TABLE sft_datasets ENABLE ROW LEVEL SECURITY;
        ALTER TABLE sft_datasets FORCE ROW LEVEL SECURITY;
        CREATE POLICY sft_datasets_isolation ON sft_datasets
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

        ALTER TABLE sft_examples ENABLE ROW LEVEL SECURITY;
        ALTER TABLE sft_examples FORCE ROW LEVEL SECURITY;
        CREATE POLICY sft_examples_isolation ON sft_examples
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
