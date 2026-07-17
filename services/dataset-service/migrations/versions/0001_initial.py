"""Initial schema: datasets, versions, profiles, lineage, outbox, idempotency,
processed_events — with RLS on every table (MASTER-FR-001).

Forward-only (MASTER-FR-060).

Deviations documented in README: monthly native partitioning for
dataset_versions / lineage_edges is deferred (TODO) — the partition key would
have to join every unique constraint; retention jobs enforce the windows.

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "datasets",
    "dataset_versions",
    "profiles",
    "lineage_edges",
    "outbox",
    "idempotency_keys",
    "processed_events",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE datasets (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            description text,
            visibility text NOT NULL DEFAULT 'workspace'
                CHECK (visibility IN ('workspace','tenant_public')),
            lifecycle text NOT NULL DEFAULT 'active'
                CHECK (lifecycle IN ('active','deprecated')),
            successor_urn text,
            status text NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','processing','ready','failed')),
            error_log jsonb,
            iceberg_table text NOT NULL,
            partition_spec jsonb,
            current_version_id uuid,
            tags text[] NOT NULL DEFAULT '{}',
            custom_metadata jsonb,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz,
            CONSTRAINT datasets_failed_requires_error
                CHECK (status <> 'failed' OR error_log IS NOT NULL)
        );

        CREATE UNIQUE INDEX ux_datasets_ws_name
            ON datasets (tenant_id, workspace_id, lower(name)) WHERE deleted_at IS NULL;
        CREATE INDEX ix_datasets_tenant_status ON datasets (tenant_id, status);
        CREATE INDEX ix_datasets_tags ON datasets USING gin (tags);

        CREATE FUNCTION dataset_search_text(name text, description text, tags text[])
        RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        RETURN name || ' ' || coalesce(description, '') || ' ' ||
               coalesce(array_to_string(tags, ' '), '');

        CREATE INDEX ix_datasets_fts ON datasets USING gin (
            to_tsvector('english', dataset_search_text(name, description, tags))
        );

        -- TODO: monthly partitioning + 25-month row retention (BRD §4.1)
        CREATE TABLE dataset_versions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            dataset_id uuid NOT NULL REFERENCES datasets(id),
            version_no int NOT NULL,
            iceberg_snapshot_id bigint NOT NULL,
            schema jsonb NOT NULL,
            schema_diff jsonb,
            breaking_change boolean NOT NULL DEFAULT false,
            row_count bigint,
            bytes bigint,
            produced_by_urn text,
            profile_id uuid,
            profile_status text NOT NULL DEFAULT 'none'
                CHECK (profile_status IN ('none','pending','running','completed','failed')),
            expired boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_versions_dataset_no UNIQUE (dataset_id, version_no),
            CONSTRAINT ux_versions_dataset_snapshot UNIQUE (dataset_id, iceberg_snapshot_id)
        );
        CREATE INDEX ix_versions_tenant_dataset
            ON dataset_versions (tenant_id, dataset_id, version_no DESC);
        CREATE INDEX ix_versions_produced_by ON dataset_versions (produced_by_urn);

        ALTER TABLE datasets
            ADD CONSTRAINT fk_datasets_current_version
            FOREIGN KEY (current_version_id) REFERENCES dataset_versions(id)
            ON DELETE SET NULL;

        CREATE TABLE profiles (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            dataset_id uuid NOT NULL,
            version_id uuid NOT NULL REFERENCES dataset_versions(id),
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','completed','failed')),
            error_category text
                CHECK (error_category IS NULL OR error_category IN
                    ('EMPTY_DATA','UNNAMED_COLUMNS','SAMPLING_FAILED','OOM','TIMEOUT','INTERNAL')),
            object_key_json text,
            object_key_html text,
            summary jsonb,
            sample jsonb,
            profiler_version text,
            attempt int NOT NULL DEFAULT 1,
            callback_token text,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL,
            CONSTRAINT profiles_summary_cap CHECK (
                summary IS NULL OR pg_column_size(summary) <= 65536
            )
        );
        CREATE INDEX ix_profiles_tenant_dataset
            ON profiles (tenant_id, dataset_id, created_at DESC);

        -- TODO: monthly partitioning, 7y retention (audit-adjacent, BRD §4.1)
        CREATE TABLE lineage_edges (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            from_urn text NOT NULL,
            to_urn text NOT NULL,
            activity text NOT NULL CHECK (activity IN
                ('ingested','transformed','trained','inferred','exported','derived')),
            run_urn text,
            properties jsonb,
            actor jsonb,
            occurred_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL
        );
        CREATE UNIQUE INDEX ux_lineage_edge
            ON lineage_edges (tenant_id, from_urn, to_urn, activity, run_urn)
            NULLS NOT DISTINCT;
        CREATE INDEX ix_lineage_from ON lineage_edges (tenant_id, from_urn);
        CREATE INDEX ix_lineage_to ON lineage_edges (tenant_id, to_urn);
        CREATE INDEX ix_lineage_trained ON lineage_edges (activity, occurred_at);

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

    # Row-level security (MASTER-FR-001). NULLIF guards the empty-string value
    # that current_setting() reports on reused connections after SET LOCAL ends;
    # rows stay invisible without an explicit tenant context either way.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
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

    # Non-privileged application role (login users are created per environment
    # with `CREATE USER ... IN ROLE dataset_app`); RLS applies to it.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dataset_app') THEN
                CREATE ROLE dataset_app NOLOGIN;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO dataset_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO dataset_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dataset_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
