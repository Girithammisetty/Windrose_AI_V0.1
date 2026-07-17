"""FORCE row-level security everywhere + promote ``memory_app`` to a
non-superuser LOGIN role (tenant-isolation remediation, cross-tenant RLS-bypass).

0001 only ENABLEd RLS (public control tables + the per-tenant ``mem_t_*``
schema tables) and created ``memory_app`` as a NOLOGIN group. ENABLE (and even
FORCE) is silently bypassed for a superuser or the table owner — and the shipped
runtime DSN connected as ``windrose``, the dev cluster's SUPERUSER (BYPASSRLS).
So tenant isolation was effectively OFF.

Fix (both parts required):
  1. FORCE ROW LEVEL SECURITY on every tenant table — the public control tables,
     every already-provisioned per-tenant schema, and (via an updated
     ``mem_provision_tenant``) every future per-tenant schema.
  2. Turn ``memory_app`` into a LOGIN role (NOSUPERUSER NOBYPASSRLS, DML only)
     and point the runtime DSN at it. Migrations + the privileged provisioning
     admin pool keep running as ``windrose`` via MEM_MIGRATE_URL /
     MEM_ADMIN_DATABASE_URL.

Forward-only (MASTER-FR-060).

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PUBLIC_TENANT_TABLES = [
    "corpora", "tenant_policies", "erasure_requests", "write_audit",
    "outbox", "processed_events", "idempotency_keys",
]


def upgrade() -> None:
    # 1a. FORCE the public control tables.
    for table in PUBLIC_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

    # 1b. FORCE every already-provisioned per-tenant schema's tables.
    op.execute(
        """
        DO $$
        DECLARE sch text;
        BEGIN
            FOR sch IN
                SELECT schema_name FROM information_schema.schemata
                WHERE schema_name LIKE 'mem\\_t\\_%'
            LOOP
                EXECUTE format('ALTER TABLE %I.memories FORCE ROW LEVEL SECURITY', sch);
                EXECUTE format('ALTER TABLE %I.rag_chunks FORCE ROW LEVEL SECURITY', sch);
            END LOOP;
        END $$;
        """
    )

    # 1c. Update the provisioning primitive so future tenant schemas FORCE too.
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
            EXECUTE format('ALTER TABLE %I.memories FORCE ROW LEVEL SECURITY', sch);
            EXECUTE format('ALTER TABLE %I.rag_chunks ENABLE ROW LEVEL SECURITY', sch);
            EXECUTE format('ALTER TABLE %I.rag_chunks FORCE ROW LEVEL SECURITY', sch);
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

    # 2. Promote memory_app to a non-privileged LOGIN role.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'memory_app') THEN
                ALTER ROLE memory_app WITH LOGIN PASSWORD 'memory_app'
                    NOSUPERUSER NOBYPASSRLS;
            ELSE
                CREATE ROLE memory_app WITH LOGIN PASSWORD 'memory_app'
                    NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO memory_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO memory_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO memory_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO memory_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO memory_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
