"""Initial schema (BRD §4.1) with RLS on every tenant table (MASTER-FR-001).
Forward-only (MASTER-FR-060).

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "pipeline_templates", "pipeline_template_versions", "pipeline_runs",
    "tenant_quotas", "run_queue", "labeled_examples", "outbox", "idempotency_keys",
    "processed_events",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pipeline_templates (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            pipeline_type smallint NOT NULL,
            model_type smallint,
            algorithm_template_name text,
            active_version_id uuid,
            is_system boolean NOT NULL DEFAULT false,
            created_by text,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE UNIQUE INDEX ux_templates_ws_name
            ON pipeline_templates (tenant_id, workspace_id, lower(name))
            WHERE deleted_at IS NULL;
        CREATE INDEX ix_templates_tenant_type
            ON pipeline_templates (tenant_id, pipeline_type, created_at DESC);

        CREATE TABLE pipeline_template_versions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            template_id uuid NOT NULL REFERENCES pipeline_templates(id),
            version_no int NOT NULL,
            definition jsonb NOT NULL,
            validation_status smallint NOT NULL DEFAULT 0,
            validation_report jsonb,
            run_parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
            global_parameters text[] NOT NULL DEFAULT '{}',
            component_catalog_version text,
            compiled_manifest_ref text,
            manifest_digest text,
            argo_template_name text UNIQUE,
            created_by text,
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_versions_template_no UNIQUE (template_id, version_no)
        );
        CREATE INDEX ix_versions_template
            ON pipeline_template_versions (template_id, created_at DESC);

        CREATE TABLE pipeline_runs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            template_id uuid NOT NULL,
            version_id uuid NOT NULL REFERENCES pipeline_template_versions(id),
            status smallint NOT NULL,
            argo_workflow_name text UNIQUE,
            mlflow_run_id text,
            run_parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
            components_status jsonb NOT NULL DEFAULT '{}'::jsonb,
            error jsonb,
            input_dataset_urns text[] NOT NULL DEFAULT '{}',
            output_dataset_urns text[] NOT NULL DEFAULT '{}',
            retried_from_run_id uuid,
            submitted_by text,
            via_agent jsonb,
            model_uri text,
            metrics jsonb,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            queued_at timestamptz,
            submitted_at timestamptz,
            started_at timestamptz,
            finished_at timestamptz
        );
        CREATE INDEX ix_runs_tenant_status
            ON pipeline_runs (tenant_id, status, created_at DESC);
        CREATE INDEX ix_runs_template ON pipeline_runs (template_id, created_at DESC);
        CREATE INDEX ix_runs_workflow ON pipeline_runs (argo_workflow_name);

        -- Global catalog tables (not tenant-scoped; no RLS).
        CREATE TABLE components (
            name text PRIMARY KEY,
            component_type smallint NOT NULL,
            internal_component_type smallint NOT NULL DEFAULT 0,
            label text NOT NULL,
            definition jsonb NOT NULL,
            yaml_ref text,
            image_digest text,
            catalog_version text NOT NULL,
            enabled boolean NOT NULL DEFAULT true
        );

        CREATE TABLE algorithm_templates (
            name text PRIMARY KEY,
            label text NOT NULL,
            model_type smallint NOT NULL,
            order_no int NOT NULL,
            model_type_order int NOT NULL,
            input_type jsonb NOT NULL,
            pipeline jsonb NOT NULL,
            tuning_pipeline jsonb NOT NULL,
            tuning_pipeline_cross_validation jsonb NOT NULL,
            parameters jsonb NOT NULL,
            tuning_parameters jsonb NOT NULL,
            metadata jsonb NOT NULL,
            catalog_version text NOT NULL,
            runnable boolean NOT NULL DEFAULT true
        );

        CREATE TABLE tenant_quotas (
            tenant_id uuid PRIMARY KEY,
            max_concurrent_runs int NOT NULL DEFAULT 10,
            max_concurrent_pods int NOT NULL DEFAULT 40,
            max_run_duration_minutes int NOT NULL DEFAULT 480,
            min_seconds_between_runs int NOT NULL DEFAULT 15,
            resource_ceiling jsonb NOT NULL DEFAULT '{}'::jsonb,
            node_pool text
        );

        CREATE TABLE run_queue (
            run_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            enqueued_at timestamptz NOT NULL
        );
        CREATE INDEX ix_queue_tenant ON run_queue (tenant_id, enqueued_at);

        CREATE TABLE labeled_examples (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            dataset_urn text NOT NULL,
            row_pk text NOT NULL,
            features jsonb NOT NULL,
            label text NOT NULL,
            source_case_urn text,
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_labeled_dataset_row UNIQUE (tenant_id, dataset_urn, row_pk)
        );
        CREATE INDEX ix_labeled_dataset ON labeled_examples (tenant_id, dataset_urn);

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

    # RLS: ENABLE turns policies on for non-owners; FORCE additionally subjects the
    # table OWNER to them so the runtime is safe even if it happens to own the tables
    # (MASTER-FR-001). The permissive worker_outbox policy is OR'd for the relay.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
            """
        )
    op.execute(
        """
        CREATE POLICY worker_outbox ON outbox
        USING (coalesce(current_setting('app.worker', true), '') = 'true');
        """
    )

    # Non-owner, non-superuser DML-only runtime role. Migrations run as a privileged
    # role (PPL_MIGRATE_URL); the runtime logs in as pipeline_app, so FORCE RLS applies.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pipeline_app') THEN
                CREATE ROLE pipeline_app LOGIN PASSWORD 'pipeline_app'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO pipeline_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pipeline_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pipeline_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
