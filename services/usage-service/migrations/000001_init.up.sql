-- usage-service schema (BRD 17 §4). Forward-only (MASTER-FR-060).
-- Real Postgres 16 meter store + rollups (TimescaleDB-style: monthly range
-- partitions for the raw hypertable, materialized rollup tables refreshed by
-- the rollup engine, retention enforced by the retention job).

-- ---- Meter catalog (global, no tenant_id) — USG-FR-001 ---------------------
CREATE TABLE meters (
    meter_key    TEXT PRIMARY KEY,
    unit         TEXT NOT NULL,
    aggregation  TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    dimensions   TEXT[] NOT NULL DEFAULT '{}',
    deprecated   BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- Raw metering (hypertable-style: monthly partitions) — USG-FR-002/020 --
CREATE TABLE usage_raw (
    time          TIMESTAMPTZ NOT NULL,
    tenant_id     UUID NOT NULL,
    meter_key     TEXT NOT NULL,
    quantity      NUMERIC(20,6) NOT NULL,
    workspace_id  TEXT,
    user_id       TEXT,
    agent_id      TEXT,
    model         TEXT,
    cloud         TEXT NOT NULL DEFAULT 'aws',
    resource_urn  TEXT,
    event_id      UUID NOT NULL,
    late          BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Idempotent ingestion: one row per (tenant, event, meter) — USG-FR-011.
    UNIQUE (tenant_id, event_id, meter_key, time)
) PARTITION BY RANGE (time);

-- Default partition catches all months; the retention/rollup job creates
-- monthly partitions ahead and detaches expired ones (raw retention 90d).
CREATE TABLE usage_raw_default PARTITION OF usage_raw DEFAULT;

CREATE INDEX usage_raw_tenant_meter_time_idx ON usage_raw (tenant_id, meter_key, time DESC);
CREATE INDEX usage_raw_tenant_ws_idx         ON usage_raw (tenant_id, workspace_id, time DESC);
CREATE INDEX usage_raw_tenant_agent_idx      ON usage_raw (tenant_id, agent_id, time DESC);

-- ---- Rollup tables (raw -> hourly -> daily -> monthly) — USG-FR-020/021 ----
-- Continuous-aggregate equivalent: materialized rollups keyed by the full
-- dimension tuple; NULL dims collapse via COALESCE sentinels in the refresh.
CREATE TABLE usage_hourly (
    bucket       TIMESTAMPTZ NOT NULL,
    tenant_id    UUID NOT NULL,
    meter_key    TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    user_id      TEXT NOT NULL DEFAULT '',
    agent_id     TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    cloud        TEXT NOT NULL DEFAULT '',
    quantity_sum NUMERIC(30,6) NOT NULL DEFAULT 0,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, meter_key, bucket, workspace_id, user_id, agent_id, model, cloud)
);
CREATE INDEX usage_hourly_tenant_meter_idx ON usage_hourly (tenant_id, meter_key, bucket DESC);

CREATE TABLE usage_daily (
    bucket       DATE NOT NULL,
    tenant_id    UUID NOT NULL,
    meter_key    TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    user_id      TEXT NOT NULL DEFAULT '',
    agent_id     TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    cloud        TEXT NOT NULL DEFAULT '',
    quantity_sum NUMERIC(30,6) NOT NULL DEFAULT 0,
    finalized_at TIMESTAMPTZ,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, meter_key, bucket, workspace_id, user_id, agent_id, model, cloud)
);
CREATE INDEX usage_daily_tenant_meter_idx ON usage_daily (tenant_id, meter_key, bucket DESC);
CREATE INDEX usage_daily_tenant_ws_idx    ON usage_daily (tenant_id, workspace_id, bucket DESC);
CREATE INDEX usage_daily_tenant_agent_idx ON usage_daily (tenant_id, agent_id, bucket DESC);

CREATE TABLE usage_monthly (
    bucket       DATE NOT NULL, -- first day of month
    tenant_id    UUID NOT NULL,
    meter_key    TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    user_id      TEXT NOT NULL DEFAULT '',
    agent_id     TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    cloud        TEXT NOT NULL DEFAULT '',
    quantity_sum NUMERIC(30,6) NOT NULL DEFAULT 0,
    finalized_at TIMESTAMPTZ,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, meter_key, bucket, workspace_id, user_id, agent_id, model, cloud)
);
CREATE INDEX usage_monthly_tenant_meter_idx ON usage_monthly (tenant_id, meter_key, bucket DESC);

-- ---- Budgets & window state — USG-FR-030/031 -------------------------------
CREATE TABLE budgets (
    id                 UUID PRIMARY KEY,
    tenant_id          UUID NOT NULL,
    scope_workspace_id TEXT,
    scope_user_id      TEXT,
    scope_agent_id     TEXT,
    meter_key          TEXT NOT NULL,
    budget_window      TEXT NOT NULL,
    limit_value        NUMERIC(20,6) NOT NULL CHECK (limit_value > 0),
    action_at_100      TEXT NOT NULL DEFAULT 'alert_only',
    status             TEXT NOT NULL DEFAULT 'active',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX budgets_tenant_status_idx ON budgets (tenant_id, status);
CREATE INDEX budgets_meter_idx ON budgets (tenant_id, meter_key, status);

CREATE TABLE budget_states (
    budget_id      UUID NOT NULL,
    tenant_id      UUID NOT NULL,
    window_start   TIMESTAMPTZ NOT NULL,
    consumed       NUMERIC(30,6) NOT NULL DEFAULT 0,
    last_threshold INT NOT NULL DEFAULT 0,
    exhausted_at   TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (budget_id, window_start)
);
CREATE INDEX budget_states_tenant_idx ON budget_states (tenant_id, budget_id);

-- ---- Rate cards — USG-FR-042 -----------------------------------------------
CREATE TABLE rate_cards (
    id             UUID PRIMARY KEY,
    tenant_id      UUID, -- NULL = default platform card
    version        INT NOT NULL,
    effective_from DATE NOT NULL,
    status         TEXT NOT NULL DEFAULT 'draft',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX rate_cards_tenant_idx ON rate_cards (tenant_id, status, effective_from DESC);

CREATE TABLE rate_card_items (
    rate_card_id       UUID NOT NULL REFERENCES rate_cards(id),
    meter_key          TEXT NOT NULL,
    price_per_unit_usd NUMERIC(14,8) NOT NULL,
    PRIMARY KEY (rate_card_id, meter_key)
);

-- ---- Anomalies — USG-FR-050/051 --------------------------------------------
CREATE TABLE anomalies (
    id                UUID PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    meter_key         TEXT NOT NULL,
    day               DATE NOT NULL,
    observed          NUMERIC(30,6) NOT NULL,
    mean              NUMERIC(30,6) NOT NULL,
    stddev            NUMERIC(30,6) NOT NULL,
    z                 DOUBLE PRECISION NOT NULL,
    status            TEXT NOT NULL DEFAULT 'open',
    dismissed_by      TEXT,
    suppressed_reason TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, meter_key, day)
);
CREATE INDEX anomalies_tenant_status_day_idx ON anomalies (tenant_id, status, day DESC);

-- ---- Reconciliation & adjustments — USG-FR-070/072 -------------------------
CREATE TABLE reconciliations (
    id         UUID PRIMARY KEY,
    month      TEXT NOT NULL,
    provider   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    report_uri TEXT NOT NULL DEFAULT '',
    detail     JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (month, provider)
);

CREATE TABLE adjustments (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    meter_key      TEXT NOT NULL,
    month          TEXT NOT NULL,
    quantity_delta NUMERIC(30,6) NOT NULL DEFAULT 0,
    usd_delta      NUMERIC(20,6) NOT NULL DEFAULT 0,
    reason         TEXT NOT NULL,
    actor          TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX adjustments_tenant_month_idx ON adjustments (tenant_id, month);

-- ---- Idempotency keys (MASTER-FR-025) --------------------------------------
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

-- ---- Transactional outbox (MASTER-FR-034) ----------------------------------
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
