-- Per-tenant embedded-UI configuration (IDN-FR-043): the embed secret (hashed)
-- the tenant's backend presents to POST /token/embed, and the allowed embedding
-- origins bound into embed tokens as the frame_ancestors claim. Platform-scoped
-- like tenants (one row per tenant); no RLS (managed by tenant admins via the
-- action-gated admin API).
CREATE TABLE tenant_embed_configs (
    tenant_id       uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    secret_hash     text NOT NULL,
    allowed_origins text[] NOT NULL DEFAULT '{}',
    updated_at      timestamptz NOT NULL DEFAULT now()
);
