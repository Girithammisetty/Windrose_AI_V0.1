-- query-service schema (BRD 05 §4.1). Forward-only (MASTER-FR-060).

CREATE TABLE saved_queries (
    id                 UUID PRIMARY KEY,
    tenant_id          UUID NOT NULL,
    workspace_id       UUID NOT NULL,
    name               TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    current_version_no INT  NOT NULL DEFAULT 1,
    tags               TEXT[] NOT NULL DEFAULT '{}',
    -- V1 SavedQuery rule: at least one module (QRY-FR-001)
    module_names       TEXT[] NOT NULL CHECK (cardinality(module_names) >= 1),
    created_by         TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at         TIMESTAMPTZ
);

-- Name unique per workspace among live rows (QRY-FR-001).
CREATE UNIQUE INDEX saved_queries_name_uniq
    ON saved_queries (tenant_id, workspace_id, lower(name))
    WHERE deleted_at IS NULL;
CREATE INDEX saved_queries_tenant_idx ON saved_queries (tenant_id, id DESC) WHERE deleted_at IS NULL;

CREATE TABLE saved_query_versions (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    saved_query_id UUID NOT NULL REFERENCES saved_queries (id),
    version_no     INT  NOT NULL,
    sql_text       TEXT NOT NULL,
    -- validated declaration array, ≤16KB (MASTER-FR-061 documented use)
    variables      JSONB NOT NULL DEFAULT '[]' CHECK (pg_column_size(variables) <= 16384),
    dataset_refs   JSONB NOT NULL DEFAULT '[]',
    created_by     TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (saved_query_id, version_no)
);
CREATE INDEX saved_query_versions_qid_idx ON saved_query_versions (tenant_id, saved_query_id, version_no DESC);

-- Query history (QRY-FR-080). Monthly partitions from day one
-- (MASTER-FR-062); 13-month retention → Iceberg archive (retention job
-- documented in README/RUNBOOK).
CREATE TABLE executions (
    id                   UUID NOT NULL,
    tenant_id            UUID NOT NULL,
    workspace_id         UUID NOT NULL,
    saved_query_id       UUID,
    query_version_no     INT,
    sql_fingerprint      TEXT NOT NULL DEFAULT '',
    sql_text_compressed  BYTEA,
    bound_params         JSONB,          -- PII-redacted (BR-12)
    caller_class         TEXT NOT NULL CHECK (caller_class IN ('user','service','agent')),
    engine               TEXT NOT NULL DEFAULT '',
    routing_reason       JSONB,
    status               TEXT NOT NULL,
    queue_position       INT,
    estimated_scan_bytes BIGINT NOT NULL DEFAULT 0,
    actual_scan_bytes    BIGINT NOT NULL DEFAULT 0,
    result_rows          BIGINT NOT NULL DEFAULT 0,
    result_bytes         BIGINT NOT NULL DEFAULT 0,
    result_uri           TEXT NOT NULL DEFAULT '',
    cache_hit            BOOLEAN NOT NULL DEFAULT false,
    cache_key            TEXT NOT NULL DEFAULT '',
    dataset_urns         JSONB NOT NULL DEFAULT '[]',
    error                JSONB,
    ceilings             JSONB,
    warnings             JSONB NOT NULL DEFAULT '[]',
    duration_ms          BIGINT NOT NULL DEFAULT 0,
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    created_by           TEXT NOT NULL,
    via_agent            JSONB,
    trace_id             TEXT NOT NULL DEFAULT '',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Default partition catches all months; the retention job creates monthly
-- partitions ahead and detaches expired ones (13-month retention).
CREATE TABLE executions_default PARTITION OF executions DEFAULT;

CREATE INDEX executions_tenant_created_idx  ON executions (tenant_id, created_at DESC);
CREATE INDEX executions_tenant_active_idx   ON executions (tenant_id, status) WHERE status IN ('queued','running','streaming_results');
CREATE INDEX executions_tenant_query_idx    ON executions (tenant_id, saved_query_id, created_at DESC);
CREATE INDEX executions_tenant_fp_idx       ON executions (tenant_id, sql_fingerprint);
CREATE INDEX executions_cache_idx           ON executions (tenant_id, cache_key, finished_at DESC) WHERE status = 'succeeded';

CREATE TABLE tenant_query_limits (
    tenant_id  UUID PRIMARY KEY,
    overrides  JSONB NOT NULL DEFAULT '{}',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE idempotency_keys (
    tenant_id  UUID NOT NULL,
    key        TEXT NOT NULL,
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    status     INT  NOT NULL,
    response   BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);

-- Transactional outbox (MASTER-FR-034).
CREATE TABLE outbox (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id     UUID NOT NULL UNIQUE,
    tenant_id    UUID NOT NULL,
    event_type   TEXT NOT NULL,
    actor_type   TEXT NOT NULL,
    actor_id     TEXT NOT NULL,
    via_agent    JSONB,
    resource_urn TEXT NOT NULL DEFAULT '',
    occurred_at  TIMESTAMPTZ NOT NULL,
    trace_id     TEXT NOT NULL DEFAULT '',
    payload      JSONB NOT NULL DEFAULT '{}',
    published_at TIMESTAMPTZ
);
CREATE INDEX outbox_unpublished_idx ON outbox (id) WHERE published_at IS NULL;
