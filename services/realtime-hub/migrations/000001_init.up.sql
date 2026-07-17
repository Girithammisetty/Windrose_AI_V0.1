-- realtime-hub metadata (BRD 20 §4). The hub is intentionally near-stateless:
-- stream_tickets is the audit copy of the Redis-hot single-use connect ticket,
-- and routing_rules is the code-seeded, ops-toggleable event_type -> topic map.

CREATE TABLE IF NOT EXISTS stream_tickets (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    subject     TEXT NOT NULL,
    topics      TEXT[] NOT NULL,
    ip_hash     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_stream_tickets_expires ON stream_tickets (expires_at);

CREATE TABLE IF NOT EXISTS routing_rules (
    id             UUID PRIMARY KEY,
    event_type     TEXT NOT NULL UNIQUE,
    topic_template TEXT NOT NULL,
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
