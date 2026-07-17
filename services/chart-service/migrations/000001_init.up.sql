-- chart-service schema (BRD 07 §4). Forward-only (MASTER-FR-060).
-- All tables carry tenant_id (MASTER-FR-001); RLS is added in 000002.

CREATE TABLE dashboards (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    workspace_id    UUID NOT NULL,
    name            TEXT NOT NULL,
    module          TEXT NOT NULL CHECK (module IN ('insights','case_management','inspector')),
    description     TEXT NOT NULL DEFAULT '',
    -- documented JSONB use (MASTER-FR-061): ordered grid placements, <=64KB
    layout          JSONB NOT NULL DEFAULT '[]' CHECK (pg_column_size(layout) <= 65536),
    -- display prefs, <=8KB
    meta            JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(meta) <= 8192),
    tags            TEXT[] NOT NULL DEFAULT '{}',
    owner_user_id   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft','active')),
    archived        BOOLEAN NOT NULL DEFAULT false,
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

-- CHART-FR-001: name unique per (workspace, module) among non-archived, live rows.
CREATE UNIQUE INDEX uq_dash_ws_name_module
    ON dashboards (tenant_id, workspace_id, module, lower(name))
    WHERE NOT archived AND deleted_at IS NULL;
CREATE INDEX ix_dash_list
    ON dashboards (tenant_id, workspace_id, module, archived, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE charts (
    id               UUID PRIMARY KEY,
    tenant_id        UUID NOT NULL,
    dashboard_id     UUID NOT NULL REFERENCES dashboards (id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    chart_type       TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    -- schema-validated per type, <=64KB
    config           JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(config) <= 65536),
    -- allow_cases, colors, legend, drilldown ref, <=16KB
    display_meta     JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(display_meta) <= 16384),
    chart_version    INT NOT NULL DEFAULT 1,
    custom           BOOLEAN NOT NULL DEFAULT true,
    config_status    TEXT NOT NULL DEFAULT 'ok' CHECK (config_status IN ('ok','broken')),
    link_type        SMALLINT CHECK (link_type IN (0,1)),
    linked_parent_id UUID REFERENCES charts (id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at       TIMESTAMPTZ
);

-- CHART-FR-010: chart name unique per dashboard among live custom charts.
CREATE UNIQUE INDEX uq_chart_dash_name
    ON charts (tenant_id, dashboard_id, lower(name))
    WHERE custom AND deleted_at IS NULL;
CREATE INDEX ix_chart_dashboard ON charts (tenant_id, dashboard_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_chart_linked_parent ON charts (linked_parent_id) WHERE linked_parent_id IS NOT NULL;

CREATE TABLE chart_sources (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    chart_id    UUID NOT NULL REFERENCES charts (id) ON DELETE CASCADE,
    position    SMALLINT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('semantic_measure','saved_query','dataset','ml_run')),
    source_urn  TEXT NOT NULL,
    UNIQUE (chart_id, position)
);
-- Reverse lookup for event-driven cache invalidation (CHART-FR-031).
CREATE INDEX ix_chart_sources_urn ON chart_sources (tenant_id, source_urn);

CREATE TABLE chart_links (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    parent_chart_id UUID NOT NULL REFERENCES charts (id) ON DELETE CASCADE,
    child_chart_id  UUID NOT NULL REFERENCES charts (id) ON DELETE CASCADE,
    -- [{parent_col, child_col}]
    linked_columns  JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (parent_chart_id, child_chart_id),
    CHECK (parent_chart_id <> child_chart_id)
);
CREATE INDEX ix_chart_links_child ON chart_links (tenant_id, child_chart_id);

CREATE TABLE documentations (
    id                UUID PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    documentable_type TEXT NOT NULL CHECK (documentable_type IN ('dashboard','chart')),
    documentable_id   UUID NOT NULL,
    content           TEXT NOT NULL DEFAULT '' CHECK (octet_length(content) <= 65536),
    archived          BOOLEAN NOT NULL DEFAULT false,
    archived_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_docs_target ON documentations (tenant_id, documentable_type, documentable_id);

-- Async export/render operations (CHART-FR-041).
CREATE TABLE operations (
    id           UUID PRIMARY KEY,
    tenant_id    UUID NOT NULL,
    chart_id     UUID,
    kind         TEXT NOT NULL,
    format       TEXT,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed')),
    artifact_url TEXT,
    artifact_urn TEXT,
    error        TEXT,
    request      JSONB NOT NULL DEFAULT '{}',
    expires_at   TIMESTAMPTZ,
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_operations_tenant ON operations (tenant_id, id DESC);

-- Idempotency-Key ledger (MASTER-FR-025).
CREATE TABLE idempotency_keys (
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL,
    idem_key      TEXT NOT NULL,
    method        TEXT NOT NULL,
    path          TEXT NOT NULL,
    status_code   INT NOT NULL,
    response_body BYTEA NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idem_key, method, path)
);

-- Transactional outbox (MASTER-FR-034).
CREATE TABLE outbox (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    UUID NOT NULL,
    event_id     UUID NOT NULL UNIQUE,
    event_type   TEXT NOT NULL,
    resource_urn TEXT NOT NULL,
    envelope     JSONB NOT NULL,
    published    BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
CREATE INDEX ix_outbox_unpublished ON outbox (id) WHERE NOT published;
