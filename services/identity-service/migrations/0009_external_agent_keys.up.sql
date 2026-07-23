-- Self-service external-agent credentials (BRD 60 WS2): a per-agent API key a
-- tenant admin mints so a CUSTOMER'S OWN agent can obtain a short-lived
-- agent_autonomous token and submit governed writes (POST /external/v1/intents)
-- without a harness-signed token. The row IS the trust anchor: an admin
-- (identity.user.admin) created it, binding this key to a specific agent
-- identity + the scopes it may carry. The exchange mints a token purely from
-- these stored fields — no dependency on the agent-registry sync — and WS1's
-- ingress still forces every external write through propose-only + four-eyes +
-- the write-proposal tier ceiling regardless.
--
-- Only the argon2 hash of the secret is stored (shown-once on create, like a
-- service-account API key). Platform-scoped like tenant_branding /
-- tenant_embed_configs (no RLS); the tenant_id column scopes admin list/revoke.
CREATE TABLE external_agent_keys (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_id      text NOT NULL,
    agent_version integer NOT NULL,
    scopes        text[] NOT NULL DEFAULT '{}',
    secret_hash   text NOT NULL,
    label         text NOT NULL DEFAULT '',
    active        boolean NOT NULL DEFAULT true,
    created_by    text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz
);

CREATE INDEX ix_external_agent_keys_tenant ON external_agent_keys (tenant_id);
