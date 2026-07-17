"""Initial schema: inference_jobs, scoring_schedules, job_queue, input/output
datasets + versions, lineage_edges, serving_endpoints (reserved), outbox,
idempotency_keys, processed_events — with RLS on every table (MASTER-FR-001).

Forward-only (MASTER-FR-060).

Deviation (documented in README): monthly native partitioning of inference_jobs
(BRD §4.1) is deferred — retention jobs enforce the 18-month window instead; the
partition key would join every unique constraint.

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "inference_jobs",
    "scoring_schedules",
    "job_queue",
    "input_datasets",
    "output_datasets",
    "output_dataset_versions",
    "lineage_edges",
    "serving_endpoints",
    "outbox",
    "idempotency_keys",
    "processed_events",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE inference_jobs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            description text,
            status smallint NOT NULL,
            model_version_urn text NOT NULL,
            model_name text,
            model_version int,
            model_stage_at_submit smallint,
            input_dataset_urn text NOT NULL,
            input_dataset_version int,
            output_dataset_urn text,
            output_dataset_version int,
            output_mode smallint NOT NULL DEFAULT 0,
            output_dataset_name text,
            parameters jsonb,
            compatibility_report jsonb,
            pipeline_run_urn text,
            components_status jsonb,
            error jsonb,
            row_count bigint,
            schedule_id uuid,
            retried_from_job_id uuid,
            submitted_by text NOT NULL,
            via_agent jsonb,
            queued_at timestamptz,
            submitted_at timestamptz,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE INDEX ix_jobs_tenant_status_created
            ON inference_jobs (tenant_id, status, created_at DESC);
        CREATE UNIQUE INDEX ux_jobs_ws_name
            ON inference_jobs (tenant_id, workspace_id, name)
            WHERE deleted_at IS NULL AND schedule_id IS NULL;
        CREATE INDEX ix_jobs_model_version
            ON inference_jobs (model_version_urn, created_at DESC);
        CREATE INDEX ix_jobs_schedule
            ON inference_jobs (schedule_id, created_at DESC);
        CREATE INDEX ix_jobs_pipeline_run ON inference_jobs (pipeline_run_urn);

        CREATE TABLE scoring_schedules (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            model_version_urn text,
            model_urn text,
            stage_selector smallint,
            input_selector jsonb NOT NULL,
            cron text,
            interval_seconds int,
            timezone text NOT NULL DEFAULT 'UTC',
            overlap_policy smallint NOT NULL DEFAULT 0,
            output jsonb NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            paused_reason text,
            consecutive_failures int NOT NULL DEFAULT 0,
            temporal_schedule_id text,
            notify_on_failure boolean NOT NULL DEFAULT true,
            last_fired_at timestamptz,
            next_fire_at timestamptz,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz,
            CONSTRAINT ck_schedule_model_xor CHECK (
                (model_version_urn IS NOT NULL)::int + (model_urn IS NOT NULL)::int = 1),
            CONSTRAINT ck_schedule_trigger_xor CHECK (
                (cron IS NOT NULL)::int + (interval_seconds IS NOT NULL)::int = 1)
        );
        CREATE UNIQUE INDEX ux_schedules_ws_name
            ON scoring_schedules (tenant_id, workspace_id, name) WHERE deleted_at IS NULL;
        CREATE UNIQUE INDEX ux_schedules_temporal_id
            ON scoring_schedules (temporal_schedule_id)
            WHERE temporal_schedule_id IS NOT NULL;
        CREATE INDEX ix_schedules_enabled ON scoring_schedules (tenant_id, enabled);

        CREATE TABLE job_queue (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            job_id uuid NOT NULL UNIQUE,
            enqueued_at timestamptz NOT NULL
        );
        CREATE INDEX ix_queue_tenant_time ON job_queue (tenant_id, enqueued_at);

        CREATE TABLE input_datasets (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            urn text NOT NULL,
            dataset_id text NOT NULL,
            version_no int NOT NULL,
            schema jsonb NOT NULL,
            storage_uri text NOT NULL,
            row_count bigint NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL
        );
        CREATE INDEX ix_input_urn ON input_datasets (tenant_id, urn, version_no DESC);

        CREATE TABLE output_datasets (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            urn text NOT NULL,
            owner_model_urn text NOT NULL,
            current_version int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE UNIQUE INDEX ux_output_ws_name
            ON output_datasets (tenant_id, workspace_id, name);

        CREATE TABLE output_dataset_versions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            dataset_id uuid NOT NULL,
            version_no int NOT NULL,
            storage_uri text NOT NULL,
            snapshot_id text NOT NULL,
            row_count bigint,
            produced_by_job_id uuid,
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_output_ver UNIQUE (dataset_id, version_no)
        );
        CREATE UNIQUE INDEX ux_output_ver_job
            ON output_dataset_versions (produced_by_job_id)
            WHERE produced_by_job_id IS NOT NULL;

        CREATE TABLE lineage_edges (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            from_urn text NOT NULL,
            to_urn text NOT NULL,
            activity text NOT NULL CHECK (activity IN ('used_by','input_to','produced')),
            run_urn text,
            properties jsonb,
            occurred_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL
        );
        CREATE UNIQUE INDEX ux_lineage_edge
            ON lineage_edges (tenant_id, from_urn, to_urn, activity, run_urn)
            NULLS NOT DISTINCT;
        CREATE INDEX ix_lineage_from ON lineage_edges (tenant_id, from_urn);
        CREATE INDEX ix_lineage_to ON lineage_edges (tenant_id, to_urn);

        CREATE TABLE serving_endpoints (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            model_version_urn text NOT NULL,
            status text NOT NULL,
            kserve_ref jsonb,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
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

    # Row-level security (MASTER-FR-001). ENABLE turns RLS on for non-owner roles;
    # FORCE also subjects the table OWNER to the policy, so isolation holds even if
    # the service ever connects as the owning role. (Superusers still bypass — the
    # runtime therefore connects as the non-superuser inference_app role below.)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
            """
        )
    # Worker sessions (outbox relay, scheduler, reaper) read across tenants.
    for table in ("outbox", "inference_jobs", "scoring_schedules"):
        op.execute(
            f"""
            CREATE POLICY worker_{table} ON {table}
            USING (coalesce(current_setting('app.worker', true), '') = 'true');
            """
        )

    # The RUNTIME connects as this NON-superuser, NON-owner login role — RLS
    # (ENABLE+FORCE) therefore actually applies to the running service, not just
    # to a test-only role. Migrations run as a privileged role; the service does
    # not. The dev password is well-known and only for local infra; production
    # injects credentials via Vault/secret and never uses this literal.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'inference_app') THEN
                CREATE ROLE inference_app LOGIN PASSWORD 'inference_app'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            ELSE
                ALTER ROLE inference_app LOGIN PASSWORD 'inference_app'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO inference_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO inference_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO inference_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
