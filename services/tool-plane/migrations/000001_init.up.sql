-- tool-plane schema (BRD 13 §4). Forward-only (MASTER-FR-060).
-- One DB / bounded context shared by tool-registry (catalog+admin) and
-- mcp-gateway (per-call enforcement).

-- Real semantic discovery: pgvector stores tool-description embeddings computed
-- by the real embedding worker (Ollama nomic-embed-text, 768-dim). TPL-FR-020/021.
CREATE EXTENSION IF NOT EXISTS vector;

-- Platform-scoped catalog of tools (BRD §4 tools). tenant_id is the reserved
-- platform tenant (all-zero uuid) so the RLS platform policy covers it.
CREATE TABLE tools (
    tool_id            TEXT PRIMARY KEY,           -- namespaced e.g. case.assign
    tenant_id          UUID NOT NULL,
    display_name       TEXT NOT NULL,
    owner_service      TEXT NOT NULL,
    owner_team         TEXT NOT NULL,
    enabled_by_default BOOLEAN NOT NULL DEFAULT true,
    side_effects       TEXT NOT NULL DEFAULT 'none'
                         CHECK (side_effects IN ('none','reversible','destructive')),
    tags               TEXT[] NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Versioned, immutable-once-published tool records (BRD §4 tool_versions).
CREATE TABLE tool_versions (
    tool_id             TEXT NOT NULL REFERENCES tools(tool_id),
    tenant_id           UUID NOT NULL,
    version             TEXT NOT NULL,             -- semver
    status              TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft','published','deprecated','retired','quarantined')),
    input_schema        JSONB NOT NULL CHECK (pg_column_size(input_schema) <= 65536),
    output_schema       JSONB NOT NULL DEFAULT '{}',
    semantic_description TEXT NOT NULL,
    permission_tier     TEXT NOT NULL
                          CHECK (permission_tier IN ('read','write-proposal','write-direct','admin')),
    cost_weight         SMALLINT NOT NULL DEFAULT 1 CHECK (cost_weight BETWEEN 1 AND 10),
    declared_sla        JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(declared_sla) <= 1024),
    side_effects        TEXT NOT NULL DEFAULT 'none',
    examples            JSONB NOT NULL DEFAULT '[]' CHECK (pg_column_size(examples) <= 32768),
    embedding           vector(768),
    embedding_model_ver TEXT NOT NULL DEFAULT '',
    deprecation_ends_at TIMESTAMPTZ,
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tool_id, version)
);
-- At most one published version per tool (BRD §4 partial idx status='published').
CREATE UNIQUE INDEX tool_versions_published_uniq
    ON tool_versions (tool_id) WHERE status = 'published';
-- Approximate-NN index for discovery (cosine). Built empty; usable once rows land.
CREATE INDEX tool_versions_embedding_idx
    ON tool_versions USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Per-tenant enablement matrix (BRD §4 tenant_tool_settings). RLS-scoped.
CREATE TABLE tenant_tool_settings (
    tenant_id            UUID NOT NULL,
    tool_id              TEXT NOT NULL,
    enabled              BOOLEAN NOT NULL DEFAULT false,
    max_tier_override    TEXT CHECK (max_tier_override IN ('read','write-proposal','write-direct','admin')),
    argument_constraints JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(argument_constraints) <= 8192),
    rate_limit_override  JSONB CHECK (pg_column_size(rate_limit_override) <= 1024),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, tool_id)
);

-- Registered MCP backends / facades (BRD §4 mcp_backends). Platform-scoped.
CREATE TABLE mcp_backends (
    name             TEXT PRIMARY KEY,
    tenant_id        UUID NOT NULL,
    internal_url     TEXT NOT NULL,
    spiffe_id        TEXT NOT NULL DEFAULT '',
    kind             TEXT NOT NULL DEFAULT 'internal' CHECK (kind IN ('internal','external')),
    egress_allowlist TEXT[] NOT NULL DEFAULT '{}',
    vault_auth_ref   TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- BYO / third-party onboarding submissions (BRD §4 byo_submissions).
CREATE TABLE byo_submissions (
    id                UUID PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    manifest          JSONB NOT NULL,
    endpoint_url      TEXT NOT NULL,
    auth_method       TEXT NOT NULL DEFAULT 'api_key' CHECK (auth_method IN ('api_key','oauth2')),
    requested_tier    TEXT NOT NULL,
    egress_description TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'pending_approval'
                        CHECK (status IN ('pending_approval','approved','rejected')),
    decided_by        TEXT NOT NULL DEFAULT '',
    decision_message  TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX byo_status_idx ON byo_submissions (status);

-- Kill switches (BRD §4 kill_switches). Postgres-backed so kill state survives
-- restart (TPL-FR-052); loaded to Redis on change.
CREATE TABLE kill_switches (
    id        UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    scope     TEXT NOT NULL CHECK (scope IN ('tool','tool_version','tool_tenant')),
    tool_id   TEXT NOT NULL,
    version   TEXT NOT NULL DEFAULT '',
    kill_tenant UUID,
    active    BOOLEAN NOT NULL DEFAULT true,
    reason    TEXT NOT NULL,                       -- required (TPL-FR-053)
    set_by    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX kill_switches_uniq
    ON kill_switches (scope, tool_id, version, coalesce(kill_tenant,'00000000-0000-0000-0000-000000000000'::uuid))
    WHERE active = true;

-- Hourly rolled-up health (BRD §4 tool_health_hourly). Partitioned by month.
CREATE TABLE tool_health_hourly (
    tool_id       TEXT NOT NULL,
    tenant_id     UUID NOT NULL,
    version       TEXT NOT NULL,
    hour          TIMESTAMPTZ NOT NULL,
    calls         BIGINT NOT NULL DEFAULT 0,
    errors_by_kind JSONB NOT NULL DEFAULT '{}',
    p50_ms        INT NOT NULL DEFAULT 0,
    p95_ms        INT NOT NULL DEFAULT 0,
    p99_ms        INT NOT NULL DEFAULT 0,
    PRIMARY KEY (tool_id, version, hour, tenant_id)
) PARTITION BY RANGE (hour);
CREATE TABLE tool_health_hourly_default PARTITION OF tool_health_hourly DEFAULT;

-- Digest-level invocation log (BRD §4 invocation_log). Partitioned by month;
-- 90-day retention (full audit in audit-service via ai.tool_invoked.v1).
CREATE TABLE invocation_log (
    id            UUID NOT NULL,
    tenant_id     UUID NOT NULL,
    agent_id      TEXT NOT NULL DEFAULT '',
    agent_version TEXT NOT NULL DEFAULT '',
    obo_sub       TEXT NOT NULL DEFAULT '',
    tool_id       TEXT NOT NULL,
    tool_version  TEXT NOT NULL DEFAULT '',
    tier          TEXT NOT NULL DEFAULT '',
    decision      TEXT NOT NULL,
    error_code    TEXT NOT NULL DEFAULT '',
    args_digest   TEXT NOT NULL DEFAULT '',
    urns          TEXT[] NOT NULL DEFAULT '{}',
    latency_ms    INT NOT NULL DEFAULT 0,
    trace_id      TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE TABLE invocation_log_default PARTITION OF invocation_log DEFAULT;
CREATE INDEX invocation_log_tenant_idx ON invocation_log (tenant_id, created_at DESC);
CREATE INDEX invocation_log_tool_idx   ON invocation_log (tool_id, decision, created_at DESC);

-- Idempotency-Key replay (MASTER-FR-025). RLS-scoped.
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
    topic        TEXT NOT NULL,
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
