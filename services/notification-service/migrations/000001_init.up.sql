-- notification-service schema (BRD 19 §4). Every table carries tenant_id
-- (MASTER-FR-001); ids are uuidv7 (MASTER-FR-021). FKs stay within this DB;
-- cross-service references are URNs (MASTER-FR-060).

CREATE TABLE subscription_rules (
    id             uuid PRIMARY KEY,
    tenant_id      uuid NOT NULL,
    scope          text NOT NULL,
    subject_type   text NOT NULL,
    subject_id     text NOT NULL,
    event_types    text[] NOT NULL,
    resource_filter jsonb NOT NULL DEFAULT '{}'::jsonb,
    channels       text[] NOT NULL,
    digest_enabled boolean NOT NULL DEFAULT false,
    digest_window  text NOT NULL DEFAULT '1h',
    active         boolean NOT NULL DEFAULT true,
    created_by     text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    CONSTRAINT resource_filter_size CHECK (pg_column_size(resource_filter) <= 4096)
);
CREATE INDEX ix_rules_tenant_active ON subscription_rules (tenant_id, active);
CREATE INDEX ix_rules_event_types ON subscription_rules USING gin (event_types);

CREATE TABLE user_preferences (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL,
    user_id           text NOT NULL,
    channel_overrides jsonb NOT NULL DEFAULT '{}'::jsonb,
    mutes             jsonb NOT NULL DEFAULT '{}'::jsonb,
    quiet_hours       jsonb,
    digest_config     jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_prefs UNIQUE (tenant_id, user_id),
    CONSTRAINT overrides_size CHECK (pg_column_size(channel_overrides) <= 8192),
    CONSTRAINT mutes_size CHECK (pg_column_size(mutes) <= 8192)
);

CREATE TABLE notifications (
    id             uuid PRIMARY KEY,
    tenant_id      uuid NOT NULL,
    user_id        text NOT NULL,
    event_id       uuid NOT NULL,
    event_type     text NOT NULL,
    severity_class text NOT NULL,
    title          text NOT NULL,
    body           text NOT NULL,
    resource_urn   text NOT NULL DEFAULT '',
    deep_link      text NOT NULL DEFAULT '',
    matched_rules  jsonb NOT NULL DEFAULT '[]'::jsonb,
    read_at        timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_notif_inbox ON notifications (tenant_id, user_id, read_at, created_at DESC);

CREATE TABLE webhook_endpoints (
    id                   uuid PRIMARY KEY,
    tenant_id            uuid NOT NULL,
    url                  text NOT NULL,
    event_types          text[] NOT NULL,
    secrets              jsonb NOT NULL DEFAULT '[]'::jsonb,
    active               boolean NOT NULL DEFAULT true,
    verified_at          timestamptz,
    circuit_state        text NOT NULL DEFAULT 'closed',
    circuit_opened_at    timestamptz,
    consecutive_failures int NOT NULL DEFAULT 0,
    created_by           text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_webhooks_tenant ON webhook_endpoints (tenant_id, active);

CREATE TABLE deliveries (
    id                  uuid PRIMARY KEY,
    tenant_id           uuid NOT NULL,
    notification_id     uuid,
    webhook_endpoint_id uuid,
    event_id            uuid NOT NULL,
    recipient           text NOT NULL,
    channel             text NOT NULL,
    provider            text NOT NULL DEFAULT '',
    status              text NOT NULL,
    provider_msg_id     text NOT NULL DEFAULT '',
    attempts            int NOT NULL DEFAULT 0,
    last_error          text NOT NULL DEFAULT '',
    next_retry_at       timestamptz,
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    -- BR-1: exactly-once per (event_id, recipient, channel). Kafka redelivery
    -- cannot double-send: the unique key makes re-delivery a no-op.
    CONSTRAINT uq_delivery UNIQUE (tenant_id, event_id, recipient, channel)
);
CREATE INDEX ix_deliveries_retry ON deliveries (tenant_id, status, next_retry_at);
CREATE INDEX ix_deliveries_endpoint ON deliveries (webhook_endpoint_id, created_at DESC);
-- Retry sweeper reads due rows across tenants (worker context).
CREATE INDEX ix_deliveries_due ON deliveries (next_retry_at) WHERE status IN ('queued','sent');

CREATE TABLE templates (
    id            uuid PRIMARY KEY,
    tenant_id     uuid,
    key           text NOT NULL,
    channel       text NOT NULL,
    locale        text NOT NULL DEFAULT 'en',
    version       int NOT NULL,
    subject_tpl   text NOT NULL DEFAULT '',
    body_html_tpl text NOT NULL DEFAULT '',
    body_text_tpl text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'draft',
    published_at  timestamptz,
    created_by    text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    -- Generated column lets the unique key treat platform-default (NULL tenant)
    -- rows as a single logical tenant (the zero uuid), per BRD 19 §4.
    coalesce_tenant uuid GENERATED ALWAYS AS (COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid)) STORED,
    CONSTRAINT uq_template UNIQUE (coalesce_tenant, key, channel, locale, version)
);
CREATE INDEX ix_templates_lookup ON templates (coalesce_tenant, key, channel, locale, status);

CREATE TABLE suppressions (
    id         uuid PRIMARY KEY,
    tenant_id  uuid NOT NULL,
    email_hash text NOT NULL,
    reason     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    cleared_at timestamptz
);
CREATE INDEX ix_suppressions ON suppressions (tenant_id, email_hash) WHERE cleared_at IS NULL;

CREATE TABLE digest_buffers (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    user_id     text NOT NULL,
    channel     text NOT NULL,
    event_class text NOT NULL,
    items       jsonb NOT NULL DEFAULT '[]'::jsonb,
    window_end  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_digest UNIQUE (tenant_id, user_id, channel, event_class),
    CONSTRAINT items_size CHECK (pg_column_size(items) <= 65536)
);
-- Digest flush sweeper reads due buffers across tenants (worker context).
CREATE INDEX ix_digest_due ON digest_buffers (window_end);

CREATE TABLE idempotency_keys (
    tenant_id  uuid NOT NULL,
    key        text NOT NULL,
    method     text NOT NULL,
    path       text NOT NULL,
    status     int NOT NULL,
    response   bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);

-- Transactional outbox (MASTER-FR-034): notification.events.v1 receipts.
CREATE TABLE outbox (
    id           bigserial PRIMARY KEY,
    event_id     uuid NOT NULL UNIQUE,
    tenant_id    uuid NOT NULL,
    event_type   text NOT NULL,
    actor_type   text NOT NULL,
    actor_id     text NOT NULL,
    via_agent    jsonb,
    resource_urn text NOT NULL DEFAULT '',
    occurred_at  timestamptz NOT NULL,
    trace_id     text NOT NULL DEFAULT '',
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    published_at timestamptz
);
CREATE INDEX ix_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;
