-- identity-service schema (forward-only, MASTER-FR-060)
-- Platform-scoped registry tables (RLS-exempt per BRD 01 §4): tenants, cells,
-- provisioning_runs, signing_keys, api_key_index, platform_versions.

CREATE TABLE cells (
    id            uuid PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    cloud         text NOT NULL CHECK (cloud IN ('aws','azure','gcp')),
    region        text NOT NULL,
    capacity      integer NOT NULL DEFAULT 500,
    tenant_count  integer NOT NULL DEFAULT 0 CHECK (tenant_count >= 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tenants (
    id                    uuid PRIMARY KEY,
    name                  text NOT NULL UNIQUE,
    display_name          text NOT NULL,
    owner_email           text NOT NULL,
    tier                  text NOT NULL CHECK (tier IN ('pool','bridge','silo')),
    cell_id               uuid REFERENCES cells(id),
    cloud                 text NOT NULL CHECK (cloud IN ('aws','azure','gcp')),
    status                text NOT NULL CHECK (status IN
        ('draft','provisioning','provision_failed','active','suspended','deleting','deleted')),
    quotas                jsonb NOT NULL CHECK (pg_column_size(quotas) <= 4096), -- <=4KB (BRD §4)
    platform_version      text NOT NULL DEFAULT 'latest',
    subdomain             text NOT NULL UNIQUE,
    k8s_namespace         text NOT NULL UNIQUE,
    schema_prefix         text NOT NULL UNIQUE,
    auto_upgrade          boolean NOT NULL DEFAULT false,
    modules               text[] NOT NULL DEFAULT '{}',
    created_by            text NOT NULL DEFAULT '',
    created_at            timestamptz NOT NULL,
    updated_at            timestamptz NOT NULL,
    deleted_at            timestamptz,
    deletion_scheduled_at timestamptz
);
CREATE INDEX tenants_status_idx ON tenants (status);
CREATE INDEX tenants_cell_idx ON tenants (cell_id);

CREATE TABLE tenant_modules (
    id         uuid PRIMARY KEY,
    tenant_id  uuid NOT NULL REFERENCES tenants(id),
    module     text NOT NULL,
    version    text NOT NULL DEFAULT 'latest',
    enabled    boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, module)
);

CREATE TABLE provisioning_runs (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES tenants(id),
    workflow_id  text NOT NULL,
    step_index   integer NOT NULL,
    step_name    text NOT NULL,
    status       text NOT NULL CHECK (status IN ('pending','running','succeeded','failed','compensated')),
    attempt      integer NOT NULL DEFAULT 0,
    error        text NOT NULL DEFAULT '',
    compensation text NOT NULL DEFAULT '',
    started_at   timestamptz,
    finished_at  timestamptz,
    UNIQUE (workflow_id, step_index)
);
CREATE INDEX provisioning_runs_tenant_idx ON provisioning_runs (tenant_id, started_at DESC);

CREATE TABLE users (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    email         text NOT NULL,
    full_name     text NOT NULL DEFAULT '',
    status        text NOT NULL CHECK (status IN ('invited','active','deactivated')),
    idp_subject   text UNIQUE,
    last_login_at timestamptz,
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL,
    deleted_at    timestamptz
);
CREATE UNIQUE INDEX users_tenant_email_uq ON users (tenant_id, lower(email));
CREATE INDEX users_tenant_idx ON users (tenant_id, id);

CREATE TABLE invitations (
    id             uuid PRIMARY KEY,
    tenant_id      uuid NOT NULL,
    user_id        uuid NOT NULL REFERENCES users(id),
    token_hash     text NOT NULL UNIQUE,
    expires_at     timestamptz NOT NULL,
    accepted_at    timestamptz,
    invalidated_at timestamptz,
    created_at     timestamptz NOT NULL,
    updated_at     timestamptz NOT NULL
);
CREATE INDEX invitations_user_idx ON invitations (tenant_id, user_id);

CREATE TABLE service_accounts (
    id                    uuid PRIMARY KEY,
    tenant_id             uuid NOT NULL,
    name                  text NOT NULL,
    secret_hash           text NOT NULL,
    old_secret_hash       text,
    old_secret_expires_at timestamptz,
    scopes                text[] NOT NULL,
    expires_at            timestamptz,
    last_used_at          timestamptz,
    revoked_at            timestamptz,
    created_at            timestamptz NOT NULL,
    updated_at            timestamptz NOT NULL,
    UNIQUE (tenant_id, name)
);
CREATE INDEX service_accounts_tenant_idx ON service_accounts (tenant_id, id);

-- Platform-scoped pre-auth lookup: api key id -> tenant (no secrets here).
CREATE TABLE api_key_index (
    sa_id     uuid PRIMARY KEY,
    tenant_id uuid NOT NULL
);

CREATE TABLE agent_principals (
    id                 uuid PRIMARY KEY,
    tenant_id          uuid NOT NULL,
    agent_id           text NOT NULL,
    agent_version      text NOT NULL,
    scopes             text[] NOT NULL DEFAULT '{}',
    autonomous_allowed boolean NOT NULL DEFAULT false,
    eval_gate_ok       boolean NOT NULL DEFAULT true,
    status             text NOT NULL CHECK (status IN ('active','killed')),
    created_at         timestamptz NOT NULL,
    updated_at         timestamptz NOT NULL,
    UNIQUE (tenant_id, agent_id, agent_version)
);

CREATE TABLE signing_keys (
    kid            text PRIMARY KEY,
    alg            text NOT NULL DEFAULT 'RS256',
    vault_ref      text NOT NULL DEFAULT '',
    public_key_pem text NOT NULL,
    not_before     timestamptz NOT NULL,
    retired_at     timestamptz,
    created_at     timestamptz NOT NULL,
    updated_at     timestamptz NOT NULL
);

CREATE TABLE idempotency_keys (
    tenant_id    uuid NOT NULL,
    key          text NOT NULL,
    request_hash text NOT NULL,
    status       integer NOT NULL,
    body         bytea NOT NULL,
    created_at   timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, key)
);

-- Transactional outbox (MASTER-FR-034), master envelope fields (MASTER-FR-031).
CREATE TABLE outbox (
    event_id     uuid PRIMARY KEY,
    event_type   text NOT NULL,
    tenant_id    uuid NOT NULL,
    actor        jsonb NOT NULL,
    via_agent    jsonb,
    resource_urn text NOT NULL,
    occurred_at  timestamptz NOT NULL,
    trace_id     text NOT NULL DEFAULT '',
    payload      jsonb NOT NULL DEFAULT '{}',
    published_at timestamptz
);
CREATE INDEX outbox_unpublished_idx ON outbox (occurred_at) WHERE published_at IS NULL;

-- Platform version registry (IDN-FR-009, Should — table only, API stubbed).
CREATE TABLE platform_versions (
    version    text PRIMARY KEY,
    released_at timestamptz NOT NULL DEFAULT now()
);
