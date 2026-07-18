"""BRD 56 inc2 — persisted entity resolution.

inc1 shipped the pure matching engine + an ephemeral resolve endpoint (compute
and throw away). inc2 persists the governed capability so decisions can run ON
resolved entities:

  * resolution_configs   — ER-FR-001 tenant-scoped, VERSIONED match rules
  * resolution_runs      — ER-FR-010/040 one execution under a config version
  * resolved_entities    — ER-FR-010 stable clusters (resolved_entity_id)
  * resolved_entity_members — ER-FR-040 lineage: which record, on what evidence
  * merge_candidates     — ER-FR-030 below-auto probable merges for four-eyes

Every table carries tenant_id and gets ENABLE + FORCE RLS + a tenant_isolation
policy (MASTER-FR-001, matching 0001/0002). The runtime role (dataset_app) is
already granted DML on ALL TABLES + DEFAULT PRIVILEGES by 0002, so no new grant.

Forward-only (MASTER-FR-060).

Revision ID: 0003
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ER_TABLES = [
    "resolution_configs",
    "resolution_runs",
    "resolved_entities",
    "resolved_entity_members",
    "merge_candidates",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE resolution_configs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            dataset_id uuid NOT NULL,
            entity_type text NOT NULL,
            version_no int NOT NULL,
            deterministic_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
            scoring_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
            blocking_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
            auto_merge_threshold double precision NOT NULL DEFAULT 0.85,
            review_threshold double precision NOT NULL DEFAULT 0.60,
            pk_column text NOT NULL,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_erconfig_version
                UNIQUE (tenant_id, dataset_id, entity_type, version_no)
        );
        CREATE INDEX ix_erconfig_dataset ON resolution_configs (tenant_id, dataset_id);

        CREATE TABLE resolution_runs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            dataset_id uuid NOT NULL,
            config_id uuid NOT NULL REFERENCES resolution_configs(id),
            entity_type text NOT NULL,
            record_count int NOT NULL DEFAULT 0,
            resolved_entity_count int NOT NULL DEFAULT 0,
            merged_cluster_count int NOT NULL DEFAULT 0,
            review_candidate_count int NOT NULL DEFAULT 0,
            status text NOT NULL DEFAULT 'completed'
                CHECK (status IN ('completed','failed')),
            created_by text NOT NULL,
            created_at timestamptz NOT NULL
        );
        CREATE INDEX ix_errun_dataset ON resolution_runs (tenant_id, dataset_id, created_at DESC);

        CREATE TABLE resolved_entities (
            resolved_entity_id text NOT NULL,
            run_id uuid NOT NULL REFERENCES resolution_runs(id),
            tenant_id uuid NOT NULL,
            dataset_id uuid NOT NULL,
            entity_type text NOT NULL,
            member_count int NOT NULL DEFAULT 1,
            confidence double precision NOT NULL DEFAULT 1.0,
            method text NOT NULL,
            PRIMARY KEY (run_id, resolved_entity_id)
        );
        CREATE INDEX ix_resolved_entities_run ON resolved_entities (tenant_id, run_id);

        CREATE TABLE resolved_entity_members (
            id uuid PRIMARY KEY,
            resolved_entity_id text NOT NULL,
            run_id uuid NOT NULL REFERENCES resolution_runs(id),
            tenant_id uuid NOT NULL,
            member_pk text NOT NULL,
            method text NOT NULL,
            evidence jsonb NOT NULL DEFAULT '[]'::jsonb
        );
        CREATE INDEX ix_rem_entity ON resolved_entity_members (tenant_id, run_id, resolved_entity_id);

        CREATE TABLE merge_candidates (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES resolution_runs(id),
            tenant_id uuid NOT NULL,
            dataset_id uuid NOT NULL,
            entity_type text NOT NULL,
            left_pk text NOT NULL,
            right_pk text NOT NULL,
            score double precision NOT NULL,
            evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected')),
            proposal_id text,
            decided_by text,
            decided_at timestamptz,
            created_at timestamptz NOT NULL
        );
        CREATE INDEX ix_merge_candidates_run
            ON merge_candidates (tenant_id, run_id, status);
        """
    )

    # ENABLE + FORCE RLS + tenant_isolation policy on every new table, matching
    # 0001/0002 (owner is bound too; the runtime logs in as non-superuser
    # dataset_app so the policy is enforced — MASTER-FR-001).
    for table in ER_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
            """
        )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
