-- audit-service metadata schema (BRD 18 §4). ClickHouse holds the append-only
-- events; Postgres holds small service metadata: chain checkpoints, WORM export
-- manifests, async job records and DLQ redrive audit rows. Forward-only
-- (MASTER-FR-060). Every table: tenant_id, created_at, updated_at.

-- Per-tenant per-day hash-chain checkpoint (AUD-FR-050). head_hash is the day's
-- current chain head; sealed_at is set when the WORM manifest lands (AUD-FR-021).
CREATE TABLE chain_heads (
    tenant_id    UUID NOT NULL,
    chain_date   DATE NOT NULL,
    head_hash    TEXT NOT NULL,
    events_count BIGINT NOT NULL DEFAULT 0,
    sealed_at    TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, chain_date)
);

-- WORM export manifests (AUD-FR-020/021/022). One row per (tenant, day, revision);
-- revision increments on supplement (late events). Objects are never overwritten.
CREATE TABLE export_manifests (
    id                   UUID PRIMARY KEY,
    tenant_id            UUID NOT NULL,
    chain_date           DATE NOT NULL,
    revision             INT  NOT NULL DEFAULT 1,
    uri                  TEXT NOT NULL DEFAULT '',
    manifest_sha256      TEXT NOT NULL DEFAULT '',
    chain_head           TEXT NOT NULL DEFAULT '',
    prev_manifest_sha256 TEXT NOT NULL DEFAULT '',
    row_count            BIGINT NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','sealed','supplemented')),
    sealed_at            TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, chain_date, revision)
);
CREATE INDEX export_manifests_lookup_idx ON export_manifests (tenant_id, chain_date, revision DESC);

-- Async compliance/export jobs (AUD-FR-032/060/061). 202 {operation_id} → poll
-- via GET /operations/:id.
CREATE TABLE async_jobs (
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL,
    kind          TEXT NOT NULL,
    params_digest TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running','succeeded','failed')),
    result_uri    TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX async_jobs_tenant_idx ON async_jobs (tenant_id, created_at DESC);

-- DLQ redrive audit trail (AUD-FR-006, AC-15).
CREATE TABLE dlq_redrives (
    id         UUID PRIMARY KEY,
    tenant_id  UUID NOT NULL,
    topic      TEXT NOT NULL,
    count      INT  NOT NULL DEFAULT 0,
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX dlq_redrives_tenant_idx ON dlq_redrives (tenant_id, created_at DESC);
