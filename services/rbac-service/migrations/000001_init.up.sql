-- rbac-service initial schema (forward-only).
-- MASTER-FR-060: id, tenant_id, created_at, updated_at on every table;
-- FKs within this DB only; cross-service references are URNs.

CREATE TABLE workspaces (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 255),
    description TEXT NOT NULL DEFAULT '',
    public      BOOLEAN NOT NULL DEFAULT false,
    created_by  TEXT NOT NULL,
    archived_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- RBC-FR-001: name unique per tenant, case-insensitive.
CREATE UNIQUE INDEX uq_workspaces_tenant_name ON workspaces (tenant_id, lower(name));
CREATE INDEX idx_workspaces_tenant ON workspaces (tenant_id, archived_at);

CREATE TABLE groups (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    name           TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 255),
    description    TEXT NOT NULL DEFAULT '',
    group_type     TEXT NOT NULL CHECK (group_type IN ('permission', 'content')),
    system         BOOLEAN NOT NULL DEFAULT false,
    auto_generated BOOLEAN NOT NULL DEFAULT false,
    meta           JSONB NOT NULL DEFAULT '{}' CHECK (pg_column_size(meta) <= 4096),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- RBC-FR-010: unique per tenant per type (V1 had no uniqueness — corrected).
CREATE UNIQUE INDEX uq_groups_tenant_type_name ON groups (tenant_id, group_type, lower(name));
CREATE INDEX idx_groups_tenant_type ON groups (tenant_id, group_type);

CREATE TABLE members (
    id         UUID PRIMARY KEY,
    tenant_id  UUID NOT NULL,
    group_id   UUID NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- RBC-FR-011: unique membership (V1 allowed duplicates — corrected).
    CONSTRAINT uq_members_group_user UNIQUE (group_id, user_id)
);
CREATE INDEX idx_members_tenant_user ON members (tenant_id, user_id);

CREATE TABLE workspace_groups (
    id           UUID PRIMARY KEY,
    tenant_id    UUID NOT NULL,
    workspace_id UUID NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    group_id     UUID NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_workspace_groups UNIQUE (workspace_id, group_id)
);
CREATE INDEX idx_workspace_groups_group ON workspace_groups (group_id);

CREATE TABLE roles (
    id         UUID PRIMARY KEY,
    tenant_id  UUID, -- NULL = system role
    name       TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 255),
    system     BOOLEAN NOT NULL DEFAULT false,
    version    INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_roles_system_tenant CHECK ((system AND tenant_id IS NULL) OR (NOT system AND tenant_id IS NOT NULL))
);
CREATE UNIQUE INDEX uq_roles_tenant_name
    ON roles (COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name));

CREATE TABLE actions (
    action           TEXT PRIMARY KEY,
    service          TEXT NOT NULL,
    resource         TEXT NOT NULL,
    verb             TEXT NOT NULL,
    workspace_scoped BOOLEAN NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    deprecated       BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE role_actions (
    role_id    UUID NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    action     TEXT NOT NULL REFERENCES actions (action),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, action)
);

CREATE TABLE group_roles (
    id         UUID PRIMARY KEY,
    tenant_id  UUID NOT NULL,
    group_id   UUID NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
    role_id    UUID NOT NULL REFERENCES roles (id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_group_roles UNIQUE (group_id, role_id)
);
CREATE INDEX idx_group_roles_role ON group_roles (role_id);

CREATE TABLE content_grants (
    id               UUID PRIMARY KEY,
    tenant_id        UUID NOT NULL,
    workspace_id     UUID NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    resource_urn     TEXT NOT NULL,
    subject_type     TEXT NOT NULL CHECK (subject_type IN ('user', 'group')),
    -- Split subject columns so group deletion cascades grants via FK
    -- (RBC-FR-012 — fixes the V1 orphaned-ACL defect).
    subject_group_id UUID REFERENCES groups (id) ON DELETE CASCADE,
    subject_user_id  TEXT,
    level            TEXT NOT NULL CHECK (level IN ('viewer', 'editor', 'owner')),
    implicit         BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_grant_subject CHECK (
        (subject_type = 'group' AND subject_group_id IS NOT NULL AND subject_user_id IS NULL) OR
        (subject_type = 'user' AND subject_user_id IS NOT NULL AND subject_group_id IS NULL)
    )
);
CREATE UNIQUE INDEX uq_content_grants
    ON content_grants (workspace_id, resource_urn, subject_type, COALESCE(subject_group_id::text, subject_user_id));
CREATE INDEX idx_content_grants_urn ON content_grants (resource_urn);
CREATE INDEX idx_content_grants_tenant ON content_grants (tenant_id);
CREATE INDEX idx_content_grants_user ON content_grants (subject_user_id) WHERE subject_user_id IS NOT NULL;
CREATE INDEX idx_content_grants_group ON content_grants (subject_group_id) WHERE subject_group_id IS NOT NULL;

-- Projection work queue (RBC-FR-042; transient).
CREATE TABLE projection_dirty (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    user_id     TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at  TIMESTAMPTZ,
    claimed_by  TEXT
);
CREATE INDEX idx_projection_dirty_claim ON projection_dirty (claimed_at NULLS FIRST, id);

-- Transactional outbox (MASTER-FR-034).
CREATE TABLE outbox (
    id           BIGSERIAL PRIMARY KEY,
    event_id     UUID NOT NULL UNIQUE,
    tenant_id    UUID NOT NULL,
    event_type   TEXT NOT NULL,
    actor_type   TEXT NOT NULL,
    actor_id     TEXT NOT NULL,
    via_agent    JSONB,
    resource_urn TEXT NOT NULL DEFAULT '',
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    trace_id     TEXT NOT NULL DEFAULT '',
    payload      JSONB NOT NULL DEFAULT '{}',
    published_at TIMESTAMPTZ
);
CREATE INDEX idx_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;

-- Idempotency-Key replay store (MASTER-FR-025; 24h retention).
CREATE TABLE idempotency_keys (
    tenant_id  UUID NOT NULL,
    key        TEXT NOT NULL,
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    status     INTEGER NOT NULL,
    response   JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);

-- Monotonic version source for projection last-writer-wins (RBC-FR-048).
CREATE SEQUENCE projection_version_seq;
