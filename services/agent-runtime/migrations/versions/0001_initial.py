"""Initial schema (BRD 14 §4). Single-schema RLS (MASTER-FR-001): every
tenant-scoped table carries ``tenant_id uuid`` and an RLS policy
``tenant_id = current_setting('app.tenant_id')::uuid`` enforced against the
non-privileged ``agent_runtime_app`` role. Platform-scoped tables (agent
definitions/versions, rollouts, kill switches) are readable by all tenants.

Immutability: published agent_versions content columns are UPDATE-blocked by a
trigger (ART-FR-002, AC-8). pgvector is enabled for A2A card-discovery embeddings.

Forward-only (MASTER-FR-060).

Revision ID: 0001
"""
# ruff: noqa: E501

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ["tenant_agent_configs", "sessions", "runs", "checkpoints", "proposals", "outbox"]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # ---- platform-scoped (no RLS) ------------------------------------------
    op.execute(
        """
        CREATE TABLE agent_definitions (
            agent_key text PRIMARY KEY,
            display_name text NOT NULL,
            description text NOT NULL DEFAULT '',
            owner_team text NOT NULL,
            default_write_mode text NOT NULL CHECK (default_write_mode IN ('read_only','proposal')),
            status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','deprecated','retired')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE agent_versions (
            agent_key text NOT NULL REFERENCES agent_definitions(agent_key),
            version int NOT NULL,
            graph_ref text NOT NULL,
            graph_digest text NOT NULL,
            prompt_refs jsonb NOT NULL DEFAULT '[]',
            toolset jsonb NOT NULL DEFAULT '[]',
            model_config jsonb NOT NULL DEFAULT '{}',
            guardrail_profile text NOT NULL DEFAULT 'standard',
            memory_policy jsonb NOT NULL DEFAULT '{}',
            eval_gate jsonb NOT NULL DEFAULT '{}',
            eval_gate_result_id text,
            a2a_card jsonb NOT NULL DEFAULT '{}',
            card_signature text,
            card_embedding vector(768),
            principal_ref text,
            status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','deprecated','retired')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (agent_key, version)
        );

        -- Immutability trigger: once published, content columns cannot change (AC-8).
        CREATE OR REPLACE FUNCTION agent_version_immutable() RETURNS trigger AS $$
        BEGIN
            IF OLD.status IN ('published','deprecated','retired') THEN
                IF NEW.graph_ref IS DISTINCT FROM OLD.graph_ref
                   OR NEW.graph_digest IS DISTINCT FROM OLD.graph_digest
                   OR NEW.prompt_refs IS DISTINCT FROM OLD.prompt_refs
                   OR NEW.toolset IS DISTINCT FROM OLD.toolset
                   OR NEW.model_config IS DISTINCT FROM OLD.model_config
                   OR NEW.a2a_card IS DISTINCT FROM OLD.a2a_card THEN
                    RAISE EXCEPTION 'agent_version content is immutable once published';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_agent_version_immutable BEFORE UPDATE ON agent_versions
            FOR EACH ROW EXECUTE FUNCTION agent_version_immutable();

        CREATE TABLE rollouts (
            rollout_id uuid PRIMARY KEY,
            agent_key text NOT NULL,
            cell text NOT NULL,
            mode text NOT NULL CHECK (mode IN ('direct','canary','shadow')),
            candidate_version int NOT NULL,
            baseline_version int NOT NULL,
            pct int NOT NULL DEFAULT 0,
            tenant_filter jsonb NOT NULL DEFAULT '{}',
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','promoted','rolled_back')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_rollouts_active ON rollouts (agent_key, cell) WHERE status = 'active';

        CREATE TABLE kill_switches (
            kill_id uuid PRIMARY KEY,
            scope text NOT NULL CHECK (scope IN ('agent','agent_version','agent_version_tenant')),
            agent_key text NOT NULL,
            version int,
            tenant_id uuid,
            active boolean NOT NULL DEFAULT true,
            reason text NOT NULL,
            set_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_kill_active ON kill_switches (agent_key) WHERE active;
        """
    )

    # ---- tenant-scoped (RLS) -----------------------------------------------
    op.execute(
        """
        CREATE TABLE tenant_agent_configs (
            tenant_id uuid NOT NULL,
            agent_key text NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            pinned_version int,
            prompt_params jsonb NOT NULL DEFAULT '{}',
            auto_execute_policy jsonb NOT NULL DEFAULT '{}',
            self_approval boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, agent_key)
        );

        CREATE TABLE sessions (
            session_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            user_id text,
            agent_key text NOT NULL,
            agent_version int NOT NULL,
            context_urn text,
            status text NOT NULL CHECK (status IN ('active','idle','terminated','expired')),
            created_at timestamptz NOT NULL DEFAULT now(),
            last_activity_at timestamptz NOT NULL DEFAULT now(),
            expires_hard_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_sessions_owner ON sessions (tenant_id, user_id, status);
        CREATE INDEX ix_sessions_hard ON sessions (expires_hard_at);

        CREATE TABLE runs (
            run_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            session_id uuid NOT NULL,
            agent_key text NOT NULL,
            agent_version int NOT NULL,
            temporal_workflow_id text,
            status text NOT NULL,
            principal_type text NOT NULL CHECK (principal_type IN ('user_obo','agent_autonomous')),
            obo_sub text,
            parent_run_id uuid,
            usage jsonb NOT NULL DEFAULT '{}',
            error jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_runs_session ON runs (tenant_id, session_id);
        CREATE UNIQUE INDEX ix_runs_wf ON runs (temporal_workflow_id) WHERE temporal_workflow_id IS NOT NULL;

        CREATE TABLE checkpoints (
            run_id uuid NOT NULL,
            checkpoint_id text NOT NULL,
            tenant_id uuid NOT NULL,
            seq int NOT NULL,
            state_ref jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, checkpoint_id)
        );
        CREATE INDEX ix_checkpoints_seq ON checkpoints (run_id, seq);

        CREATE TABLE proposals (
            proposal_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            session_id uuid,
            run_id uuid NOT NULL,
            agent_key text NOT NULL,
            agent_version int NOT NULL,
            obo_user text,
            tool_id text NOT NULL,
            tool_version text NOT NULL,
            tier text NOT NULL,
            side_effects text NOT NULL DEFAULT 'reversible',
            args jsonb NOT NULL DEFAULT '{}',
            rationale text NOT NULL DEFAULT '',
            affected_urns text[] NOT NULL DEFAULT '{}',
            predicted_effect jsonb NOT NULL DEFAULT '{}',
            expires_at timestamptz NOT NULL,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected','edited_approved','expired','superseded','cancelled')),
            decision jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_proposals_inbox ON proposals (tenant_id, status, expires_at);
        CREATE INDEX ix_proposals_urns ON proposals USING gin (affected_urns);
        CREATE INDEX ix_proposals_run ON proposals (run_id);

        CREATE TABLE outbox (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            topic text NOT NULL,
            payload jsonb NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        );
        CREATE INDEX ix_outbox_unpub ON outbox (occurred_at) WHERE published_at IS NULL;
        """
    )

    # ---- non-privileged app role + RLS -------------------------------------
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_runtime_app') THEN
                CREATE ROLE agent_runtime_app NOLOGIN;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO agent_runtime_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agent_runtime_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agent_runtime_app;
        """
    )
    for t in TENANT_TABLES:
        op.execute(
            f"""
            ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {t}_isolation ON {t}
                USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
            """
        )


def downgrade() -> None:
    for t in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE;")
    op.execute(
        "DROP TABLE IF EXISTS kill_switches, rollouts, agent_versions, agent_definitions CASCADE;"
    )
    op.execute("DROP FUNCTION IF EXISTS agent_version_immutable() CASCADE;")
