"""Initial schema — forward-only (MASTER-FR-060..063).

Row-level security on every table with the mandated policy
`tenant_id = current_setting('app.tenant_id')::uuid` (MASTER-FR-001), and
monthly partitioning for the high-volume tables (ingestions,
ingestion_transitions) via a DEFAULT partition; per-month partition management
is a pg_partman TODO (wave-2).

Revision ID: 0001
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

RLS_TABLES = (
    "connections",
    "ingestions",
    "uploads",
    "upload_parts",
    "schedules",
    "ingestion_transitions",
    "webhook_endpoints",
    "webhook_event_dedup",
    "outbox",
    "idempotency_keys",
)

DDL = """
CREATE TABLE connections (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    name text NOT NULL,
    connector_type text NOT NULL CHECK (connector_type IN (
        'postgres','mysql','mariadb','oracle','sqlserver','synapse','presto',
        'bigquery','snowflake','s3','azure_blob','gcs','sftp','ftp','http_api')),
    config jsonb NOT NULL CHECK (pg_column_size(config) <= 16384),
    vault_ref text,
    secret_field_names jsonb NOT NULL DEFAULT '[]'::jsonb,
    traffic_direction text NOT NULL DEFAULT 'incoming'
        CHECK (traffic_direction IN ('incoming','outgoing','both')),
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    last_test_status text,
    last_tested_at timestamptz,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);
CREATE UNIQUE INDEX uq_connections_tenant_ws_name
    ON connections (tenant_id, workspace_id, lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX ix_connections_tenant_type ON connections (tenant_id, connector_type);

CREATE TABLE ingestions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    connection_id uuid,
    dataset_urn text,
    new_dataset jsonb,
    ingestion_mode text NOT NULL
        CHECK (ingestion_mode IN ('file_upload','query','scheduled_run','webhook_batch')),
    file_format text CHECK (file_format IS NULL OR file_format IN
        ('csv','tsv','json','jsonl','parquet','avro')),
    statement text,
    status text NOT NULL DEFAULT 'created' CHECK (status IN (
        'created','awaiting_upload','queued','running','committing','retrying',
        'completed','failed','cancelled','expired')),
    trigger text NOT NULL DEFAULT 'manual'
        CHECK (trigger IN ('manual','schedule','webhook','agent')),
    schedule_id uuid,
    scheduled_for timestamptz,
    skip_profiling boolean NOT NULL DEFAULT false,
    allow_empty boolean NOT NULL DEFAULT false,
    bytes_total bigint,
    bytes_received bigint NOT NULL DEFAULT 0 CHECK (bytes_received >= 0),
    rows_appended bigint NOT NULL DEFAULT 0,
    iceberg_snapshot_id bigint,
    error_log jsonb CHECK (error_log IS NULL OR pg_column_size(error_log) <= 65536),
    error_row_limit integer NOT NULL DEFAULT 100 CHECK (error_row_limit BETWEEN 0 AND 10000),
    attempts integer NOT NULL DEFAULT 0,
    retried_from_id uuid,
    started_at timestamptz,
    finished_at timestamptz,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE TABLE ingestions_default PARTITION OF ingestions DEFAULT;
CREATE INDEX ix_ingestions_tenant_status_created
    ON ingestions (tenant_id, status, created_at DESC);
CREATE INDEX ix_ingestions_tenant_dataset_created
    ON ingestions (tenant_id, dataset_urn, created_at DESC);
CREATE INDEX ix_ingestions_connection_active ON ingestions (connection_id)
    WHERE status NOT IN ('completed','failed','cancelled','expired');

CREATE TABLE uploads (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    ingestion_id uuid NOT NULL,
    part_size integer NOT NULL,
    storage_prefix text NOT NULL,
    cloud_upload_id text,
    sha256 text,
    bytes_total bigint,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','completing','completed','expired','aborted')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_uploads_tenant_status_expires ON uploads (tenant_id, status, expires_at);

CREATE TABLE upload_parts (
    upload_id uuid NOT NULL REFERENCES uploads (id) ON DELETE CASCADE,
    n integer NOT NULL CHECK (n >= 1),
    tenant_id uuid NOT NULL,
    etag text NOT NULL,
    size bigint NOT NULL,
    storage_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (upload_id, n)
);

CREATE TABLE schedules (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    connection_id uuid NOT NULL REFERENCES connections (id),
    ingestion_template jsonb NOT NULL CHECK (pg_column_size(ingestion_template) <= 16384),
    cron text,
    interval_seconds integer,
    timezone text NOT NULL,
    watermark_column text,
    watermark_operator text NOT NULL DEFAULT '>',
    watermark_value_type text NOT NULL DEFAULT 'string'
        CHECK (watermark_value_type IN ('int','decimal','timestamp','date','string')),
    watermark_value text,
    overlap_policy text NOT NULL DEFAULT 'skip' CHECK (overlap_policy IN ('skip','buffer_one')),
    enabled boolean NOT NULL DEFAULT true,
    temporal_schedule_id text NOT NULL,
    last_fired_at timestamptz,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);
CREATE INDEX ix_schedules_tenant_enabled ON schedules (tenant_id, enabled);

CREATE TABLE ingestion_transitions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    ingestion_id uuid NOT NULL,
    from_status text,
    to_status text NOT NULL,
    actor jsonb,
    detail jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE TABLE ingestion_transitions_default PARTITION OF ingestion_transitions DEFAULT;
CREATE INDEX ix_transitions_tenant_ingestion
    ON ingestion_transitions (tenant_id, ingestion_id, created_at);

CREATE TABLE webhook_endpoints (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    ingestion_id uuid NOT NULL,
    path_token text NOT NULL UNIQUE,
    hmac_vault_ref text NOT NULL,
    flush_interval_s integer NOT NULL DEFAULT 60,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE webhook_event_dedup (
    ingestion_id uuid NOT NULL,
    event_id text NOT NULL,
    tenant_id uuid NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ingestion_id, event_id)
);

CREATE TABLE outbox (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    event_id uuid NOT NULL UNIQUE,
    event_type text NOT NULL,
    resource_urn text NOT NULL,
    actor jsonb NOT NULL,
    via_agent jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    trace_id text,
    payload jsonb NOT NULL,
    published_at timestamptz
);
CREATE INDEX ix_outbox_unpublished ON outbox (occurred_at) WHERE published_at IS NULL;

CREATE TABLE idempotency_keys (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    key text NOT NULL,
    request_hash text NOT NULL,
    status_code integer,
    response_body jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_idempotency_tenant_key UNIQUE (tenant_id, key)
);
"""


def upgrade() -> None:
    op.execute(DDL)
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id')::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid)"
        )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
