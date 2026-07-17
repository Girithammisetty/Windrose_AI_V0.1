"""SLM distillation — milestone 1: the agent-run transcript corpus.

A durable, tenant-isolated, PII-redacted record of every completed agent run:
the inputs + grounding the agent saw, the answer/proposed action it produced,
the model/cost, and — joined in when the proposal is decided — the HUMAN
decision (approve / edit / reject) and the corrected output. An approved or
edited proposal is a gold (input -> corrected-output) training pair; this table
is the corpus SFT curation reads from (docs/design/slm-distillation.md).

Capture is consent-gated (``consent`` column records whether the tenant was
opted in at capture time) so curation only ever trains on consented data.

Forward-only (MASTER-FR-060). Revision ID: 0006
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_transcripts (
            transcript_id    uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            run_id           uuid NOT NULL,
            session_id       uuid,
            agent_key        text NOT NULL,
            agent_version    int NOT NULL,
            principal_type   text NOT NULL,
            obo_sub          text,
            inputs           jsonb NOT NULL DEFAULT '{}',
            grounding        jsonb NOT NULL DEFAULT '{}',
            final_text       text,
            proposed_action  jsonb,
            proposal_id      uuid,
            model            text,
            usage            jsonb NOT NULL DEFAULT '{}',
            consent          boolean NOT NULL DEFAULT false,
            -- human decision (the training signal), attached when the proposal is decided
            decision         text,
            corrected_output jsonb,
            decided_by       text,
            decided_at       timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_transcripts_agent ON agent_transcripts (tenant_id, agent_key, created_at DESC);
        CREATE INDEX ix_transcripts_run ON agent_transcripts (tenant_id, run_id);
        CREATE UNIQUE INDEX ix_transcripts_proposal ON agent_transcripts (proposal_id)
            WHERE proposal_id IS NOT NULL;

        GRANT SELECT, INSERT, UPDATE, DELETE ON agent_transcripts TO agent_runtime_app;

        ALTER TABLE agent_transcripts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent_transcripts FORCE ROW LEVEL SECURITY;
        CREATE POLICY agent_transcripts_isolation ON agent_transcripts
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
