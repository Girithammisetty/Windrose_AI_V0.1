-- case-service schema (BRD 08 §4). Forward-only (MASTER-FR-060).
-- Row-reference triage model: cases store dataset_urn + row_pk + a small
-- display projection, NEVER a full-row snapshot while open (CASE-FR-001).

-- Per-workspace monotonic case number allocator (CASE-FR-004): replaces V1's
-- pg_advisory_xact_lock with a sequence row locked FOR UPDATE.
CREATE TABLE case_sequences (
    tenant_id    UUID NOT NULL,
    workspace_id UUID NOT NULL,
    last_number  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, workspace_id)
);

-- Disposition catalog (CASE-FR-020), workspace-configurable.
CREATE TABLE dispositions (
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL,
    workspace_id  UUID NOT NULL,
    code          TEXT NOT NULL,
    label         TEXT NOT NULL,
    category      TEXT NOT NULL CHECK (category IN ('true_positive','false_positive','benign','inconclusive','other')),
    requires_note BOOLEAN NOT NULL DEFAULT false,
    active        BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, workspace_id, code)
);

-- Custom case fields (CASE-FR-022). purpose smallint: 0=create,1=update,2=both.
CREATE TABLE case_fields (
    id           UUID PRIMARY KEY,
    tenant_id    UUID NOT NULL,
    workspace_id UUID NOT NULL,
    query_urn    TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL,
    data_type    TEXT NOT NULL CHECK (data_type IN ('string','text','integer','float','boolean','date','enum')),
    purpose      SMALLINT NOT NULL DEFAULT 2 CHECK (purpose IN (0,1,2)),
    field_meta   JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(field_meta) <= 8192),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at   TIMESTAMPTZ,
    -- Unique per (workspace, coalesced query scope, name), CASE-FR-022.
    UNIQUE (tenant_id, workspace_id, query_urn, name)
);

-- SLA policy per workspace (CASE-FR-012).
CREATE TABLE sla_policies (
    tenant_id          UUID NOT NULL,
    workspace_id       UUID NOT NULL,
    warn_before        INTERVAL NOT NULL DEFAULT '24 hours',
    on_breach          TEXT NOT NULL DEFAULT 'auto_unassign' CHECK (on_breach IN ('auto_unassign','escalate','notify_only')),
    escalate_to        UUID,
    max_reassign_count INT NOT NULL DEFAULT 3,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id)
);

-- Cases (CASE-FR-001/010). Status: draft(0), in_progress(1), resolved(2),
-- unassigned(3), closed(4). The V1 invariant is DB-enforced (BR-1).
CREATE TABLE cases (
    id                 UUID PRIMARY KEY,
    tenant_id          UUID NOT NULL,
    workspace_id       UUID NOT NULL,
    case_number        BIGINT NOT NULL,
    status             SMALLINT NOT NULL CHECK (status IN (0,1,2,3,4)),
    severity           TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('low','medium','high','critical')),
    assigned_to_id     UUID,
    assigned_to_at     TIMESTAMPTZ,
    created_by_id      TEXT NOT NULL,
    dataset_urn        TEXT NOT NULL,
    dataset_version    TEXT NOT NULL DEFAULT '',
    row_pk             TEXT NOT NULL,
    dedup_key          TEXT,
    display_projection JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(display_projection) <= 4096),
    projection_truncated BOOLEAN NOT NULL DEFAULT false,
    source_query_urns  TEXT[] NOT NULL DEFAULT '{}',
    dashboard_urn      TEXT NOT NULL DEFAULT '',
    due_date           TIMESTAMPTZ NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    custom_fields      JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(custom_fields) <= 16384),
    disposition_id     UUID,
    resolution_note    TEXT NOT NULL DEFAULT '',
    resolved_at        TIMESTAMPTZ,
    closed_at          TIMESTAMPTZ,
    snapshot_ref       TEXT NOT NULL DEFAULT '',
    recurrence_of      UUID,
    reassign_count     INT NOT NULL DEFAULT 0,
    row_unavailable    BOOLEAN NOT NULL DEFAULT false,
    case_version       INT NOT NULL DEFAULT 1,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at         TIMESTAMPTZ,
    -- V1 invariant, DB-enforced (CASE-FR-010, BR-1).
    CONSTRAINT cases_assignee_status_invariant CHECK ((assigned_to_id IS NULL) = (status = 3))
);

CREATE UNIQUE INDEX cases_number_uniq ON cases (tenant_id, workspace_id, case_number);
-- Open-case dedup: one open case per (workspace, dataset_urn+row_pk) key
-- (CASE-FR-005, BR-2). Closed cases (status=4) are exempt so recurrence works.
CREATE UNIQUE INDEX cases_dedup_uniq ON cases (tenant_id, workspace_id, dedup_key)
    WHERE dedup_key IS NOT NULL AND status <> 4;
CREATE INDEX cases_status_due_idx  ON cases (tenant_id, workspace_id, status, due_date);
CREATE INDEX cases_assignee_idx    ON cases (tenant_id, assigned_to_id, status);
CREATE INDEX cases_dataset_idx     ON cases (tenant_id, dataset_urn);

-- Activity timeline (CASE-FR-025), append-only, partitioned monthly
-- (MASTER-FR-062). Composite PK includes the partition key.
CREATE TABLE case_events (
    id           UUID NOT NULL,
    tenant_id    UUID NOT NULL,
    case_id      UUID NOT NULL,
    event_type   TEXT NOT NULL,
    actor_type   TEXT NOT NULL,
    actor_id     TEXT NOT NULL,
    via_agent    JSONB,
    proposal_urn TEXT,
    old_value    JSONB,
    new_value    JSONB,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE TABLE case_events_default PARTITION OF case_events DEFAULT;
CREATE INDEX case_events_case_idx ON case_events (tenant_id, case_id, occurred_at);

-- Comments (CASE-FR-024).
CREATE TABLE case_comments (
    id         UUID PRIMARY KEY,
    tenant_id  UUID NOT NULL,
    case_id    UUID NOT NULL,
    author_id  TEXT NOT NULL,
    body       TEXT NOT NULL,
    edited_at  TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX case_comments_case_idx ON case_comments (tenant_id, case_id, created_at);

-- Durable SLA timers (CASE-FR-012/013). Temporal-equivalent when Temporal is
-- absent: a Postgres-backed durable store swept by the SLA worker. Survives
-- restarts (AC-4) because timer state lives in Postgres, not process memory.
CREATE TABLE sla_timers (
    tenant_id      UUID NOT NULL,
    case_id        UUID NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('warn','due')),
    fire_at        TIMESTAMPTZ NOT NULL,
    case_version   INT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','fired','cancelled')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (case_id, kind)
);
CREATE INDEX sla_timers_due_idx ON sla_timers (fire_at) WHERE status = 'pending';

-- Applied-proposal idempotency (CASE-FR-051, BR-9): proposal_urn unique.
CREATE TABLE applied_proposals (
    tenant_id    UUID NOT NULL,
    proposal_urn TEXT NOT NULL,
    case_id      UUID NOT NULL,
    response     JSONB NOT NULL DEFAULT '{}',
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, proposal_urn)
);

-- Async bulk / export operations (CASE-FR-030/044).
CREATE TABLE operations (
    id           UUID PRIMARY KEY,
    tenant_id    UUID NOT NULL,
    workspace_id UUID NOT NULL,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',
    succeeded    INT NOT NULL DEFAULT 0,
    failed       INT NOT NULL DEFAULT 0,
    total        INT NOT NULL DEFAULT 0,
    result       JSONB NOT NULL DEFAULT '{}',
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX operations_tenant_idx ON operations (tenant_id, id DESC);

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
