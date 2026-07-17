"""Initial schema: provider_deployments, model_ladders, budgets, ledger tables,
virtual_keys, guardrail_policies, semantic_cache_entries (pgvector),
request_log, tenant_configs, outbox, idempotency_keys, processed_events —
with RLS on every tenant table (MASTER-FR-001).

Forward-only (MASTER-FR-060).

Deviations documented in README:
- budget_spend / request_log monthly native partitioning deferred (TODO): the
  partition key would join every unique constraint; retention jobs enforce the
  24-month / 90-day windows instead.
- ledger tables (budget_spend, budget_reservations, budget_threshold_flags)
  are keyed by budget_ref (already tenant-scoped via budgets) and accessed
  only under the worker GUC; they carry no tenant_id column.

Revision ID: 0001
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "provider_deployments",
    "model_ladders",
    "budgets",
    "virtual_keys",
    "guardrail_policies",
    "semantic_cache_entries",
    "request_log",
    "outbox",
    "idempotency_keys",
    "processed_events",
]

WORKER_TABLES = ["budget_spend", "budget_reservations", "budget_threshold_flags"]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        """
        CREATE TABLE provider_deployments (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            provider text NOT NULL CHECK (provider IN ('azure_openai','bedrock','vertex','anthropic')),
            model_family text NOT NULL,
            deployment_name text NOT NULL,
            region text NOT NULL,
            cloud text NOT NULL CHECK (cloud IN ('aws','azure','gcp')),
            endpoint_vault_ref text NOT NULL,
            tpm_limit integer NOT NULL DEFAULT 0,
            rpm_limit integer NOT NULL DEFAULT 0,
            priority integer NOT NULL DEFAULT 100,
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','draining','disabled')),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE INDEX idx_provider_deployments_routing
            ON provider_deployments (cloud, status, priority);

        CREATE TABLE model_ladders (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            request_class text NOT NULL CHECK (request_class IN ('chat','sql-gen','judge','embed')),
            scope text NOT NULL CHECK (scope IN ('platform','tenant')),
            rungs jsonb NOT NULL CHECK (pg_column_size(rungs) <= 8192),
            version integer NOT NULL DEFAULT 1,
            max_rung smallint,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz,
            UNIQUE (tenant_id, request_class, scope)
        );
        CREATE INDEX idx_model_ladders_class ON model_ladders (request_class);

        CREATE TABLE budgets (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            scope_type text NOT NULL CHECK (scope_type IN ('platform','tenant','workspace','principal','virtual_key')),
            scope_ref text NOT NULL,
            "window" text NOT NULL CHECK ("window" IN ('daily','monthly')),
            limit_usd numeric(12,4) NOT NULL CHECK (limit_usd >= 0),
            degrade_pct smallint NOT NULL DEFAULT 95 CHECK (degrade_pct BETWEEN 1 AND 100),
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz,
            UNIQUE (tenant_id, scope_type, scope_ref, "window")
        );

        -- Ledger tables: worker-only access; monthly partitioning deferred (TODO)
        CREATE TABLE budget_spend (
            budget_ref text NOT NULL,
            window_start text NOT NULL,
            spend_cents bigint NOT NULL DEFAULT 0,
            reserved_cents bigint NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL,
            PRIMARY KEY (budget_ref, window_start)
        );
        CREATE TABLE budget_reservations (
            id text PRIMARY KEY,
            budget_ref text NOT NULL,
            window_start text NOT NULL,
            amount_cents bigint NOT NULL,
            expires_at timestamptz NOT NULL
        );
        CREATE INDEX idx_budget_reservations_ref
            ON budget_reservations (budget_ref, window_start);
        CREATE INDEX idx_budget_reservations_exp ON budget_reservations (expires_at);
        CREATE TABLE budget_threshold_flags (
            flag_key text PRIMARY KEY,
            created_at timestamptz NOT NULL
        );

        CREATE TABLE virtual_keys (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            key_hash text NOT NULL UNIQUE,
            principal_type text NOT NULL CHECK (principal_type IN ('user','agent','service')),
            principal_id text NOT NULL,
            allowed_request_classes text[] NOT NULL DEFAULT '{}',
            max_rung smallint NOT NULL DEFAULT 2,
            expires_at timestamptz,
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz
        );
        CREATE INDEX idx_virtual_keys_principal ON virtual_keys (tenant_id, principal_id);

        CREATE TABLE guardrail_policies (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            policy jsonb NOT NULL CHECK (pg_column_size(policy) <= 8192),
            version integer NOT NULL DEFAULT 1,
            current boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            deleted_at timestamptz,
            UNIQUE (tenant_id, version)
        );

        CREATE TABLE semantic_cache_entries (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            prompt_hash char(64) NOT NULL,
            context_hash char(64) NOT NULL,
            embedding vector(1536),
            response jsonb NOT NULL,
            workspace_id uuid,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL
        );
        CREATE INDEX idx_cache_lookup
            ON semantic_cache_entries (tenant_id, prompt_hash, context_hash);
        -- ivfflat index (filtered by tenant via RLS); lists tuned per cell size
        CREATE INDEX idx_cache_embedding ON semantic_cache_entries
            USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

        CREATE TABLE request_log (
            request_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            principal text NOT NULL,
            request_class text NOT NULL,
            model_alias text NOT NULL DEFAULT '',
            rung smallint NOT NULL DEFAULT 0,
            input_tokens bigint NOT NULL DEFAULT 0,
            output_tokens bigint NOT NULL DEFAULT 0,
            cost_usd double precision NOT NULL DEFAULT 0,
            cached boolean NOT NULL DEFAULT false,
            guardrail_flags text[] NOT NULL DEFAULT '{}',
            status text NOT NULL,
            latency_ms integer NOT NULL DEFAULT 0,
            trace_id text,
            deployment_id text,
            created_at timestamptz NOT NULL
        );
        CREATE INDEX idx_request_log_tenant_time ON request_log (tenant_id, created_at);

        CREATE TABLE tenant_configs (
            tenant_id uuid PRIMARY KEY,
            timezone text NOT NULL DEFAULT 'UTC',
            cell_cloud text,
            cache_ttl_seconds integer,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );

        CREATE TABLE outbox (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            topic text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            published_at timestamptz
        );
        CREATE INDEX idx_outbox_unpublished ON outbox (created_at) WHERE published_at IS NULL;

        CREATE TABLE idempotency_keys (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            key text NOT NULL,
            request_hash text NOT NULL,
            status_code integer NOT NULL,
            body jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            UNIQUE (tenant_id, key)
        );

        CREATE TABLE processed_events (
            event_id text PRIMARY KEY,
            tenant_id uuid NOT NULL,
            created_at timestamptz NOT NULL
        );
        """
    )

    # Row-level security (MASTER-FR-001). NULLIF guards the empty-string value
    # current_setting() reports on reused connections after SET LOCAL ends.
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
    # tenant_configs is projected data (read per-tenant, written by consumer);
    # same tenant policy applies.
    op.execute(
        """
        ALTER TABLE tenant_configs ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation_tenant_configs ON tenant_configs
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )
    # Virtual-key authentication crosses tenants (hash lookup before the tenant
    # is known): dedicated keyauth GUC policy, SELECT-only in practice.
    op.execute(
        """
        CREATE POLICY keyauth_virtual_keys ON virtual_keys
        USING (coalesce(current_setting('app.keyauth', true), '') = 'true');
        """
    )
    # Worker paths: outbox dispatcher + budget ledger read/write across tenants.
    op.execute(
        """
        CREATE POLICY worker_outbox ON outbox
        USING (coalesce(current_setting('app.worker', true), '') = 'true');
        """
    )
    for table in WORKER_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY worker_{table} ON {table}
            USING (coalesce(current_setting('app.worker', true), '') = 'true');
            """
        )

    # Non-privileged application role (login users are created per environment
    # with `CREATE USER ... IN ROLE ai_gateway_app`); RLS applies to it.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_gateway_app') THEN
                CREATE ROLE ai_gateway_app NOLOGIN;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO ai_gateway_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ai_gateway_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_gateway_app;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
