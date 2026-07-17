"""Initial eval schema: datasets, eval_cases, scorers, suites, eval_runs,
case_results, gate_results, canary_comparisons, slo_rollups, outbox,
processed_events — with RLS on every table (MASTER-FR-001). Forward-only.

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "datasets",
    "eval_cases",
    "scorers",
    "suites",
    "eval_runs",
    "case_results",
    "gate_results",
    "canary_comparisons",
    "slo_rollups",
    "outbox",
    "processed_events",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE datasets (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            dataset_key text NOT NULL,
            agent_key text NOT NULL,
            version int NOT NULL,
            status text NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','frozen','archived')),
            description text,
            case_count int NOT NULL DEFAULT 0,
            provenance_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            frozen_by text,
            frozen_at timestamptz,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT ux_datasets_key_version UNIQUE (tenant_id, dataset_key, version)
        );
        CREATE INDEX ix_datasets_agent ON datasets (tenant_id, agent_key);

        CREATE TABLE eval_cases (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            dataset_key text NOT NULL,
            dataset_version int NOT NULL,
            input jsonb NOT NULL,
            expected jsonb NOT NULL,
            source text NOT NULL CHECK (source IN
                ('verified_query','production_trace','hitl_rejection',
                 'approval_edit_diff','manual')),
            source_ref text,
            source_tenant_id uuid,
            tags text[] NOT NULL DEFAULT '{}',
            weight double precision NOT NULL DEFAULT 1.0,
            status text NOT NULL DEFAULT 'candidate'
                CHECK (status IN ('candidate','active','retired')),
            anonymization_attested_by text,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT eval_cases_input_cap CHECK (pg_column_size(input) <= 65536),
            CONSTRAINT eval_cases_expected_cap CHECK (pg_column_size(expected) <= 65536)
        );
        CREATE INDEX ix_cases_dataset_status
            ON eval_cases (dataset_key, dataset_version, status);
        CREATE INDEX ix_cases_source_status ON eval_cases (source, status);
        CREATE INDEX ix_cases_tags ON eval_cases USING gin (tags);

        CREATE TABLE scorers (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            scorer_key text NOT NULL,
            version int NOT NULL,
            kind text NOT NULL CHECK (kind IN ('deterministic','llm_judge')),
            gate_eligible boolean NOT NULL DEFAULT false,
            config_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
            applicable_expected_kinds text[] NOT NULL DEFAULT '{}',
            image_ref text,
            judge_prompt_ref text,
            judge_prompt_ver text,
            judge_agreement double precision,
            status text NOT NULL DEFAULT 'active'
                CHECK (status IN ('draft','active','retired')),
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_scorers_key_version UNIQUE (tenant_id, scorer_key, version)
        );

        CREATE TABLE suites (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            suite_id text NOT NULL,
            agent_key text NOT NULL,
            version int NOT NULL,
            datasets jsonb NOT NULL DEFAULT '[]'::jsonb,
            scorers jsonb NOT NULL DEFAULT '[]'::jsonb,
            gate_rule text NOT NULL,
            baseline_version text,
            judge_ladder_pin jsonb NOT NULL DEFAULT '{}'::jsonb,
            min_cases int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_suites_id_version UNIQUE (tenant_id, suite_id, version)
        );

        CREATE TABLE eval_runs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            trigger text NOT NULL CHECK (trigger IN
                ('ci','publish_gate','scheduled_online','canary','manual')),
            agent_key text NOT NULL,
            candidate jsonb NOT NULL DEFAULT '{}'::jsonb,
            baseline jsonb,
            suite_pins jsonb NOT NULL DEFAULT '{}'::jsonb,
            memory_snapshot_ver text,
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','scoring','completed','failed')),
            totals jsonb NOT NULL DEFAULT '{}'::jsonb,
            cost_usd double precision NOT NULL DEFAULT 0.0,
            cost_cap_usd double precision NOT NULL DEFAULT 0.0,
            temporal_workflow_id text,
            started_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE INDEX ix_runs_agent_trigger ON eval_runs (agent_key, trigger, created_at);

        CREATE TABLE case_results (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            case_id uuid NOT NULL,
            scorer_key text NOT NULL,
            scorer_version int NOT NULL DEFAULT 1,
            score double precision NOT NULL DEFAULT 0.0,
            passed boolean NOT NULL DEFAULT false,
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            trace_ref text,
            latency_ms int,
            cost_usd double precision NOT NULL DEFAULT 0.0,
            weight double precision NOT NULL DEFAULT 1.0,
            created_at timestamptz NOT NULL,
            CONSTRAINT case_results_details_cap CHECK (pg_column_size(details) <= 32768)
        );
        CREATE INDEX ix_case_results_run ON case_results (run_id);

        CREATE TABLE gate_results (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            gate_run_id text NOT NULL,
            run_id uuid NOT NULL,
            agent_key text NOT NULL,
            content_digest text NOT NULL,
            suite_id text NOT NULL,
            suite_version int NOT NULL DEFAULT 1,
            dataset_version int NOT NULL DEFAULT 1,
            gate_passed boolean NOT NULL DEFAULT false,
            verdicts jsonb NOT NULL DEFAULT '[]'::jsonb,
            failed_cases_sample jsonb NOT NULL DEFAULT '[]'::jsonb,
            report_url text,
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_gate_addressable UNIQUE
                (agent_key, content_digest, suite_id, suite_version, dataset_version)
        );
        CREATE INDEX ix_gate_lookup ON gate_results (agent_key, content_digest);

        CREATE TABLE canary_comparisons (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            comparison_id text NOT NULL,
            agent_key text NOT NULL,
            candidate_version text NOT NULL,
            baseline_version text NOT NULL,
            sample_spec jsonb NOT NULL DEFAULT '{}'::jsonb,
            mode text NOT NULL CHECK (mode IN ('paired_shadow','split_live')),
            status text NOT NULL DEFAULT 'collecting'
                CHECK (status IN ('collecting','ready','failed_early','expired')),
            report jsonb NOT NULL DEFAULT '{}'::jsonb,
            samples int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE INDEX ix_canary_agent_status ON canary_comparisons (agent_key, status);

        CREATE TABLE slo_rollups (
            id uuid PRIMARY KEY,
            tenant_id uuid,
            agent_key text NOT NULL,
            agent_version text,
            window_name text NOT NULL,
            window_start timestamptz NOT NULL,
            counters jsonb NOT NULL DEFAULT '{}'::jsonb,
            targets jsonb NOT NULL DEFAULT '{}'::jsonb,
            sample_n int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE UNIQUE INDEX ux_slo_rollup ON slo_rollups
            (agent_key, coalesce(agent_version,''), coalesce(tenant_id,
             '00000000-0000-0000-0000-000000000000'::uuid), window_name, window_start);

        CREATE TABLE outbox (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            topic text NOT NULL,
            event_type text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            published_at timestamptz
        );
        CREATE INDEX ix_outbox_unpublished ON outbox (created_at) WHERE published_at IS NULL;

        CREATE TABLE processed_events (
            event_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            created_at timestamptz NOT NULL
        );
        """
    )

    # Row-level security (MASTER-FR-001). FORCE so RLS applies even to the table
    # owner — the runtime role (eval_app_rt) is a non-owner, non-superuser DML role,
    # and superusers bypass RLS regardless, so the runtime must never be one.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            );
            """
        )
    # The outbox dispatcher reads across tenants (worker session only).
    op.execute(
        """
        CREATE POLICY worker_outbox ON outbox
        USING (coalesce(current_setting('app.worker', true), '') = 'true');
        """
    )
    # slo_rollups platform rows may carry a NULL tenant_id (cross-tenant rollups);
    # a worker/operator read policy exposes them alongside tenant-scoped rows.
    op.execute(
        """
        CREATE POLICY platform_slo ON slo_rollups
        USING (tenant_id IS NULL AND coalesce(current_setting('app.worker', true), '') = 'true');
        """
    )

    # AC-15 defense-in-depth: a DB trigger rejects any INSERT/UPDATE/DELETE of a
    # case belonging to a FROZEN dataset version (copy-on-write to the next draft is
    # the only path). Enforced at the storage layer independent of the app.
    op.execute(
        """
        CREATE FUNCTION eval_block_frozen_case() RETURNS trigger AS $fn$
        DECLARE st text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT status INTO st FROM datasets
                    WHERE dataset_key = OLD.dataset_key
                      AND version = OLD.dataset_version
                      AND tenant_id = OLD.tenant_id;
                IF st = 'frozen' THEN
                    RAISE EXCEPTION 'dataset % v% is frozen; cases are immutable (AC-15)',
                        OLD.dataset_key, OLD.dataset_version;
                END IF;
                RETURN OLD;
            ELSE
                SELECT status INTO st FROM datasets
                    WHERE dataset_key = NEW.dataset_key
                      AND version = NEW.dataset_version
                      AND tenant_id = NEW.tenant_id;
                IF st = 'frozen' THEN
                    RAISE EXCEPTION 'dataset % v% is frozen; cases are immutable (AC-15)',
                        NEW.dataset_key, NEW.dataset_version;
                END IF;
                RETURN NEW;
            END IF;
        END;
        $fn$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_block_frozen_case
            BEFORE INSERT OR UPDATE OR DELETE ON eval_cases
            FOR EACH ROW EXECUTE FUNCTION eval_block_frozen_case();
        """
    )

    # Non-privileged application group role; RLS applies to its members.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'eval_app') THEN
                CREATE ROLE eval_app NOLOGIN;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO eval_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eval_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eval_app;
        """
    )

    # The shipped default runtime login role: a non-owner, non-superuser member of
    # eval_app. RLS (FORCE) is enforced against it. Production overrides both the
    # DSN and the password via secrets; this makes the default DSN work locally.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'eval_app_rt') THEN
                CREATE ROLE eval_app_rt LOGIN PASSWORD 'eval_app_dev' IN ROLE eval_app;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
