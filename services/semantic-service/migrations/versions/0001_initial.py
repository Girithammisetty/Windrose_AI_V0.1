"""Initial schema: semantic models, versions, projections, verified queries,
compile log, operations, chart refs, outbox, idempotency, processed_events —
with RLS on every table (MASTER-FR-001). Forward-only (MASTER-FR-060).

Deviations documented in README:
- compile_log monthly native partitioning deferred (TODO); retention job
  enforces the 6-month window.
- verified_queries.embedding requires the pgvector extension (integration tests
  use the pgvector/pgvector:pg16 image); HNSW index on embedding.

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "semantic_models",
    "model_versions",
    "entities",
    "dimensions",
    "measures",
    "join_paths",
    "verified_queries",
    "compile_log",
    "operations",
    "chart_refs",
    "outbox",
    "idempotency_keys",
    "processed_events",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        """
        CREATE TABLE semantic_models (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name text NOT NULL,
            description text,
            published_version_id uuid,
            health jsonb,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE UNIQUE INDEX ux_models_ws_name
            ON semantic_models (tenant_id, workspace_id, lower(name))
            WHERE deleted_at IS NULL;
        CREATE INDEX ix_models_tenant_ws ON semantic_models (tenant_id, workspace_id);

        CREATE TABLE model_versions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_id uuid NOT NULL REFERENCES semantic_models(id),
            version_no int NOT NULL,
            status text NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','in_review','published','rejected','superseded')),
            definition jsonb NOT NULL,
            diff jsonb,
            submitted_by text,
            approved_by text,
            decision_note text,
            published_at timestamptz,
            created_at timestamptz NOT NULL,
            CONSTRAINT ux_versions_model_no UNIQUE (model_id, version_no),
            CONSTRAINT versions_definition_cap CHECK (
                pg_column_size(definition) <= 262144
            )
        );
        CREATE INDEX ix_versions_tenant_model
            ON model_versions (tenant_id, model_id, version_no DESC);
        CREATE UNIQUE INDEX ux_versions_one_open
            ON model_versions (model_id)
            WHERE status IN ('draft','in_review','rejected');

        -- Normalized projections rebuilt from the published definition (§4.1)
        CREATE TABLE entities (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_version_id uuid NOT NULL REFERENCES model_versions(id),
            name text NOT NULL,
            dataset_urn text NOT NULL,
            physical_table text NOT NULL,
            version_policy jsonb NOT NULL,
            primary_key jsonb NOT NULL,
            CONSTRAINT ux_entities_version_name UNIQUE (model_version_id, name)
        );
        CREATE INDEX ix_entities_tenant_version_name
            ON entities (tenant_id, model_version_id, name);
        CREATE INDEX ix_entities_dataset ON entities (tenant_id, dataset_urn);

        CREATE TABLE dimensions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_version_id uuid NOT NULL REFERENCES model_versions(id),
            entity_name text NOT NULL,
            name text NOT NULL,
            "column" text,
            expr_ast jsonb,
            dim_type text NOT NULL
                CHECK (dim_type IN ('categorical','time','numeric','boolean','geo')),
            time_grains text[] NOT NULL DEFAULT '{}',
            synonyms text[] NOT NULL DEFAULT '{}',
            deprecated boolean NOT NULL DEFAULT false,
            successor text,
            CONSTRAINT ux_dimensions_version_name UNIQUE (model_version_id, name)
        );
        CREATE INDEX ix_dimensions_tenant_version_name
            ON dimensions (tenant_id, model_version_id, name);

        CREATE TABLE measures (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_version_id uuid NOT NULL REFERENCES model_versions(id),
            entity_name text,
            name text NOT NULL,
            agg text CHECK (agg IS NULL OR agg IN
                ('sum','avg','min','max','count','count_distinct','first')),
            expr_ast jsonb,
            filters_ast jsonb,
            synonyms text[] NOT NULL DEFAULT '{}',
            deprecated boolean NOT NULL DEFAULT false,
            successor text,
            CONSTRAINT ux_measures_version_name UNIQUE (model_version_id, name)
        );
        CREATE INDEX ix_measures_tenant_version_name
            ON measures (tenant_id, model_version_id, name);

        CREATE TABLE join_paths (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_version_id uuid NOT NULL REFERENCES model_versions(id),
            name text NOT NULL,
            from_entity text NOT NULL,
            to_entity text NOT NULL,
            join_type text NOT NULL CHECK (join_type IN ('left','inner')),
            on_pairs jsonb NOT NULL,
            cardinality text NOT NULL
                CHECK (cardinality IN ('many_to_one','one_to_one')),
            CONSTRAINT ux_join_paths_version_name UNIQUE (model_version_id, name)
        );

        CREATE TABLE verified_queries (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            model_id uuid,
            nl_text text NOT NULL,
            sql_text text NOT NULL,
            variables jsonb NOT NULL DEFAULT '[]',
            status text NOT NULL DEFAULT 'draft' CHECK (status IN
                ('draft','pending_review','approved','rejected','archived')),
            tags text[] NOT NULL DEFAULT '{}',
            provenance jsonb,
            health_note text,
            embedding vector(768),
            submitted_by text NOT NULL,
            approved_by text,
            decided_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE INDEX ix_vq_tenant_ws_status
            ON verified_queries (tenant_id, workspace_id, status);
        -- ANN over approved pairs; every query carries hard tenant/ws predicates
        CREATE INDEX ix_vq_embedding_hnsw ON verified_queries
            USING hnsw (embedding vector_cosine_ops);

        -- TODO: monthly partitioning, 6-month retention (BRD §4.1)
        CREATE TABLE compile_log (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            model_version_id uuid NOT NULL,
            request_hash text NOT NULL,
            request jsonb NOT NULL,
            caller_class text NOT NULL,
            dialect text NOT NULL,
            warnings jsonb NOT NULL DEFAULT '[]',
            duration_ms int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL
        );
        CREATE INDEX ix_compile_log_tenant_created
            ON compile_log (tenant_id, created_at DESC);

        CREATE TABLE operations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            kind text NOT NULL,
            status text NOT NULL CHECK (status IN ('running','completed','failed')),
            resource_urn text NOT NULL,
            report jsonb,
            created_at timestamptz NOT NULL,
            finished_at timestamptz
        );

        CREATE TABLE chart_refs (
            tenant_id uuid NOT NULL,
            chart_urn text NOT NULL,
            model text,
            measures text[] NOT NULL DEFAULT '{}',
            PRIMARY KEY (tenant_id, chart_urn)
        );
        CREATE INDEX ix_chart_refs_measures ON chart_refs USING gin (measures);

        CREATE TABLE outbox (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            topic text NOT NULL,
            event_type text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            published_at timestamptz
        );
        CREATE INDEX ix_outbox_unpublished ON outbox (created_at)
            WHERE published_at IS NULL;

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
    # with `CREATE USER ... IN ROLE semantic_app`); RLS applies to it.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'semantic_app') THEN
                CREATE ROLE semantic_app NOLOGIN;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO semantic_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
            TO semantic_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO semantic_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
