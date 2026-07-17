"""Decision outcome labels (BRD 55) — realized outcomes joined to decisions.

The Decision-Monitoring capability: a decision (a proposal — agent, decision
table, or persona copilot) later receives a REALIZED outcome (from a human, the
tenant SoR, or an event). Effectiveness = agreement between the decided and the
realized outcome, sliced by decision type / producer / time. Tenant-scoped
(RLS via tenant_id); one label per decision_ref (a later label supersedes on
conflict — outcomes get corrected). Forward-only (MASTER-FR-060).

Revision ID: 0011
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outcome_labels (
            id              uuid PRIMARY KEY,
            tenant_id       text NOT NULL,
            decision_ref    text NOT NULL,   -- proposal id (or a case/decision urn)
            decision_type   text NOT NULL,   -- the tool_id / disposition family
            producer        text,            -- agent_key / decision-model id
            decided_outcome text,            -- what the platform decided
            realized_outcome text NOT NULL,  -- what actually happened
            correct         boolean,         -- decided == realized (computed)
            label_source    text NOT NULL DEFAULT 'human'
                            CHECK (label_source IN ('human','sor','event')),
            note            text,
            labeled_by      text,
            labeled_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_outcome_decision UNIQUE (tenant_id, decision_ref)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_outcome_labels_tenant "
               "ON outcome_labels (tenant_id, decision_type);")
