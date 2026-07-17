"""Initial schema: experiments, runs + mirror child tables, registered models,
versions, promotions, registration log, model cards, mirror inbox, watermarks,
outbox, idempotency, processed_events — with RLS on every tenant table
(MASTER-FR-001). Forward-only (MASTER-FR-060).

Deviation (documented in README): run_metric_history is specced monthly-
partitioned; native partitioning is deferred (the partition key would join every
unique constraint) — the retention job enforces the 12-month hot window.

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "experiments", "runs", "run_params", "run_metrics", "run_metric_history",
    "run_tags", "run_artifacts", "run_notes", "registered_models", "model_versions",
    "promotions", "model_registration_log", "model_cards", "mirror_inbox",
    "reconciliation_watermarks", "outbox", "idempotency_keys", "processed_events",
]

# tables the background workers enumerate cross-tenant (read-only worker session)
WORKER_READ_TABLES = ["experiments", "mirror_inbox", "promotions"]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE experiments (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            description text,
            model_type smallint NOT NULL,
            mlflow_experiment_id text NOT NULL,
            model_pipeline_urn text NOT NULL,
            feature_engineering_pipeline_urn text NOT NULL,
            training_pipeline_urn text NOT NULL,
            note text,
            tags jsonb NOT NULL DEFAULT '{}',
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE UNIQUE INDEX ux_experiments_ws_name
            ON experiments (tenant_id, workspace_id, lower(name)) WHERE deleted_at IS NULL;
        CREATE UNIQUE INDEX ux_experiments_mlflow ON experiments (mlflow_experiment_id);
        CREATE INDEX ix_experiments_tenant ON experiments (tenant_id, created_at DESC);

        CREATE TABLE runs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            experiment_id uuid NOT NULL REFERENCES experiments(id),
            mlflow_run_id text NOT NULL,
            name text,
            status smallint NOT NULL,
            algorithm text NOT NULL DEFAULT '',
            artifact_uri text,
            pipeline_run_urn text,
            input_dataset_urns text[] NOT NULL DEFAULT '{}',
            output_dataset_urns text[] NOT NULL DEFAULT '{}',
            error_messages jsonb,
            duration_ms bigint,
            started_at timestamptz,
            ended_at timestamptz,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE UNIQUE INDEX ux_runs_mlflow ON runs (mlflow_run_id);
        CREATE INDEX ix_runs_tenant_experiment ON runs (tenant_id, experiment_id, created_at DESC);
        CREATE INDEX ix_runs_tenant_status ON runs (tenant_id, status);
        CREATE INDEX ix_runs_algorithm ON runs (algorithm);

        CREATE TABLE run_params (
            run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            key text NOT NULL,
            tenant_id uuid NOT NULL,
            value text NOT NULL,
            is_hidden boolean NOT NULL DEFAULT false,
            param_conflict boolean NOT NULL DEFAULT false,
            PRIMARY KEY (run_id, key)
        );
        CREATE INDEX ix_run_params_kv ON run_params (tenant_id, key, value);

        CREATE TABLE run_metrics (
            run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            key text NOT NULL,
            tenant_id uuid NOT NULL,
            value double precision NOT NULL,
            step bigint NOT NULL DEFAULT 0,
            logged_at timestamptz NOT NULL,
            PRIMARY KEY (run_id, key)
        );
        CREATE INDEX ix_run_metrics_kv ON run_metrics (tenant_id, key, value DESC);

        -- TODO: monthly partitioning + 12-month hot retention (BRD §4.1/4.3)
        CREATE TABLE run_metric_history (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            key text NOT NULL,
            step bigint NOT NULL DEFAULT 0,
            value double precision NOT NULL,
            logged_at timestamptz NOT NULL
        );
        CREATE INDEX ix_run_metric_history ON run_metric_history (run_id, key, step);
        CREATE INDEX ix_run_metric_history_tenant ON run_metric_history (tenant_id);

        CREATE TABLE run_tags (
            run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            key text NOT NULL,
            tenant_id uuid NOT NULL,
            value text NOT NULL,
            PRIMARY KEY (run_id, key)
        );
        CREATE INDEX ix_run_tags_kv ON run_tags (tenant_id, key, value);

        CREATE TABLE run_artifacts (
            run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            path text NOT NULL,
            tenant_id uuid NOT NULL,
            size_bytes bigint NOT NULL DEFAULT 0,
            content_type text,
            PRIMARY KEY (run_id, path)
        );

        CREATE TABLE run_notes (
            run_id uuid PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL,
            description text NOT NULL,
            updated_at timestamptz NOT NULL
        );

        CREATE TABLE registered_models (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            model_type smallint NOT NULL,
            description text,
            owner_id uuid NOT NULL,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE UNIQUE INDEX ux_models_ws_name
            ON registered_models (tenant_id, workspace_id, lower(name))
            WHERE deleted_at IS NULL;

        CREATE TABLE model_versions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_id uuid NOT NULL REFERENCES registered_models(id),
            version int NOT NULL,
            source_run_id uuid NOT NULL REFERENCES runs(id),
            mlflow_model_ref text,
            flavor text NOT NULL DEFAULT 'mlflow.sklearn',
            input_schema jsonb,
            output_schema jsonb,
            stage smallint NOT NULL DEFAULT 0,
            stage_updated_at timestamptz,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz,
            CONSTRAINT ux_model_versions UNIQUE (model_id, version)
        );
        -- at most one production version per model (single-production invariant)
        CREATE UNIQUE INDEX ux_model_single_production
            ON model_versions (model_id) WHERE stage = 2;
        CREATE INDEX ix_model_versions_stage ON model_versions (tenant_id, stage);

        CREATE TABLE promotions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_version_id uuid NOT NULL REFERENCES model_versions(id),
            target_stage smallint NOT NULL,
            from_stage smallint NOT NULL,
            status smallint NOT NULL DEFAULT 0,
            rationale text,
            requested_by text NOT NULL,
            via_agent jsonb,
            workflow_id text,
            decision jsonb,
            decided_at timestamptz,
            expires_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE INDEX ix_promotions_status ON promotions (tenant_id, status, created_at DESC);
        -- at most one PENDING promotion per version (BR-4)
        CREATE UNIQUE INDEX ux_promotions_one_pending
            ON promotions (model_version_id) WHERE status = 0;

        CREATE TABLE model_registration_log (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_version_id uuid NOT NULL,
            experiment_id uuid NOT NULL,
            run_snapshot jsonb NOT NULL,
            registered_by text NOT NULL,
            via_agent jsonb,
            created_at timestamptz NOT NULL,
            CONSTRAINT registration_snapshot_cap CHECK (pg_column_size(run_snapshot) <= 65536)
        );
        CREATE INDEX ix_registration_log ON model_registration_log (tenant_id, model_version_id);

        CREATE TABLE model_cards (
            model_version_id uuid PRIMARY KEY REFERENCES model_versions(id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL,
            auto_fields jsonb NOT NULL,
            overlay jsonb NOT NULL DEFAULT '{}',
            overlay_updated_by text,
            overlay_version int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );

        CREATE TABLE mirror_inbox (
            delivery_id text PRIMARY KEY,
            tenant_id uuid NOT NULL,
            event_type text NOT NULL,
            payload jsonb NOT NULL,
            received_at timestamptz NOT NULL,
            applied_at timestamptz,
            error text
        );
        CREATE INDEX ix_mirror_inbox_unapplied
            ON mirror_inbox (tenant_id, received_at) WHERE applied_at IS NULL;

        CREATE TABLE reconciliation_watermarks (
            tenant_id uuid NOT NULL,
            mlflow_experiment_id text NOT NULL,
            last_reconciled_at timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, mlflow_experiment_id)
        );

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

        CREATE TABLE idempotency_keys (
            tenant_id uuid NOT NULL,
            key text NOT NULL,
            request_hash text NOT NULL,
            status_code int NOT NULL,
            response_body jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, key)
        );

        CREATE TABLE processed_events (
            event_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            created_at timestamptz NOT NULL
        );
        """
    )

    # Row-level security (MASTER-FR-001).
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
            """
        )
    # Worker read policies for cross-tenant enumeration by the background loops.
    for table in WORKER_READ_TABLES:
        op.execute(
            f"""
            CREATE POLICY worker_read_{table} ON {table}
            FOR SELECT
            USING (coalesce(current_setting('app.worker', true), '') = 'true');
            """
        )
    # The outbox dispatcher reads/updates across tenants (worker session only).
    op.execute(
        """
        CREATE POLICY worker_outbox ON outbox
        USING (coalesce(current_setting('app.worker', true), '') = 'true');
        """
    )

    # Non-privileged application role (login users created per environment with
    # `CREATE USER ... IN ROLE experiment_app`); RLS applies to it.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'experiment_app') THEN
                CREATE ROLE experiment_app NOLOGIN;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO experiment_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO experiment_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO experiment_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
