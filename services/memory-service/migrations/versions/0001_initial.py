"""Initial schema (MEM §4). Control tables live in ``public`` with RLS
(MASTER-FR-001); tenant data (memories, rag_chunks) lives in per-tenant schemas
``mem_t_<tenant>`` created by ``mem_provision_tenant`` — the tenant-provisioning
consumer's primitive (BR-14). Forward-only (MASTER-FR-060).

pgvector required (vector(768) = nomic-embed-text dimensionality); integration
tests use the pgvector/pgvector:pg16 image with HNSW cosine indexes.

Revision ID: 0001
"""
# ruff: noqa: E501

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

PUBLIC_TENANT_TABLES = [
    "corpora", "tenant_policies", "erasure_requests", "write_audit",
    "outbox", "processed_events", "idempotency_keys",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # ---- control-plane tables (public, RLS) --------------------------------
    op.execute(
        """
        CREATE TABLE corpora (
            corpus_key text NOT NULL,
            tenant_id uuid NOT NULL,
            source jsonb NOT NULL,
            chunking jsonb NOT NULL,
            active_embedding_ver text NOT NULL,
            refresh jsonb NOT NULL,
            anonymization_profile jsonb,
            status text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','paused','rebuilding')),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, corpus_key)
        );

        CREATE TABLE tenant_policies (
            tenant_id uuid PRIMARY KEY,
            ttl_overrides jsonb NOT NULL DEFAULT '{}',
            pii_classes text[] NOT NULL DEFAULT '{}',
            injection_profile text NOT NULL DEFAULT 'standard',
            corpus_flags jsonb NOT NULL DEFAULT '{}',
            updated_at timestamptz NOT NULL
        );

        CREATE TABLE erasure_requests (
            request_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            subject_type text NOT NULL,
            subject_id text NOT NULL,
            status text NOT NULL
                CHECK (status IN ('received','running','verifying','completed','failed')),
            temporal_workflow_id text,
            report jsonb,
            created_at timestamptz NOT NULL,
            completed_at timestamptz,
            CONSTRAINT erasure_report_cap CHECK (
                report IS NULL OR pg_column_size(report) <= 65536)
        );
        CREATE INDEX ix_erasure_tenant_status ON erasure_requests (tenant_id, status);

        CREATE TABLE write_audit (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            memory_id uuid,
            action text NOT NULL
                CHECK (action IN ('write','merge','quarantine','edit','delete','expire')),
            actor jsonb NOT NULL,
            reason text,
            trace_id text,
            created_at timestamptz NOT NULL
        );
        CREATE INDEX ix_write_audit_tenant_mem ON write_audit (tenant_id, memory_id);

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

        CREATE TABLE idempotency_keys (
            tenant_id uuid NOT NULL,
            key text NOT NULL,
            request_hash text NOT NULL,
            status_code int NOT NULL,
            response_body jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            PRIMARY KEY (tenant_id, key)
        );
        """
    )

    for table in PUBLIC_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
            """
        )
    # Outbox relay reads across tenants under the worker policy.
    op.execute(
        """
        CREATE POLICY worker_outbox ON outbox
        USING (coalesce(current_setting('app.worker', true), '') = 'true');
        """
    )

    # ---- non-privileged application role -----------------------------------
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'memory_app') THEN
                CREATE ROLE memory_app NOLOGIN;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO memory_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO memory_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO memory_app;
        """
    )

    # ---- per-tenant schema provisioning primitive (BR-14) ------------------
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION mem_provision_tenant(p_tenant uuid) RETURNS void AS $fn$
        DECLARE sch text := 'mem_t_' || replace(p_tenant::text, '-', '');
        BEGIN
            EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', sch);
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO memory_app', sch);

            EXECUTE format($ddl$
                CREATE TABLE IF NOT EXISTS %I.memories (
                    memory_id uuid PRIMARY KEY,
                    tenant_id uuid NOT NULL,
                    scope text NOT NULL CHECK (scope IN ('user','workspace','tenant')),
                    scope_ref text NOT NULL,
                    content text NOT NULL,
                    embedding vector(768),
                    provenance jsonb NOT NULL,
                    confidence real NOT NULL,
                    ttl_expires_at timestamptz NOT NULL,
                    revalidate_at timestamptz NOT NULL,
                    tags text[] NOT NULL DEFAULT '{}',
                    status text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','quarantined','expired','deleted')),
                    retrieval_count int NOT NULL DEFAULT 0,
                    last_retrieved_at timestamptz,
                    classifier_score real,
                    merged_from uuid[] NOT NULL DEFAULT '{}',
                    created_at timestamptz NOT NULL,
                    updated_at timestamptz NOT NULL
                )$ddl$, sch);

            EXECUTE format($ddl$
                CREATE TABLE IF NOT EXISTS %I.rag_chunks (
                    chunk_id uuid PRIMARY KEY,
                    tenant_id uuid NOT NULL,
                    corpus_key text NOT NULL,
                    source_urn text NOT NULL,
                    chunk_seq int NOT NULL,
                    content text NOT NULL,
                    embedding vector(768),
                    embedding_model_ver text NOT NULL,
                    snapshot_ver text,
                    source_updated_at timestamptz,
                    user_linkage text,
                    created_at timestamptz NOT NULL,
                    CONSTRAINT ux_chunk UNIQUE (corpus_key, source_urn, chunk_seq, embedding_model_ver)
                )$ddl$, sch);

            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS ix_mem_scope ON %I.memories (scope, scope_ref, status)', sch);
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS ix_mem_ttl ON %I.memories (ttl_expires_at)', sch);
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS ix_mem_tags ON %I.memories USING gin (tags)', sch);
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS ix_mem_hnsw ON %I.memories '
                'USING hnsw (embedding vector_cosine_ops) WHERE status = ''active''', sch);
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS ix_chunk_src ON %I.rag_chunks (corpus_key, source_urn)', sch);
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS ix_chunk_hnsw ON %I.rag_chunks '
                'USING hnsw (embedding vector_cosine_ops)', sch);

            EXECUTE format('ALTER TABLE %I.memories ENABLE ROW LEVEL SECURITY', sch);
            EXECUTE format('ALTER TABLE %I.rag_chunks ENABLE ROW LEVEL SECURITY', sch);
            IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = sch
                           AND tablename = 'memories' AND policyname = 'tenant_iso') THEN
                EXECUTE format(
                    'CREATE POLICY tenant_iso ON %I.memories USING '
                    '(tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)', sch);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = sch
                           AND tablename = 'rag_chunks' AND policyname = 'tenant_iso') THEN
                EXECUTE format(
                    'CREATE POLICY tenant_iso ON %I.rag_chunks USING '
                    '(tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)', sch);
            END IF;
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON %I.memories TO memory_app', sch);
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON %I.rag_chunks TO memory_app', sch);
        END;
        $fn$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION mem_drop_tenant(p_tenant uuid) RETURNS void AS $fn$
        DECLARE sch text := 'mem_t_' || replace(p_tenant::text, '-', '');
        BEGIN
            EXECUTE format('DROP SCHEMA IF EXISTS %I CASCADE', sch);
        END;
        $fn$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
