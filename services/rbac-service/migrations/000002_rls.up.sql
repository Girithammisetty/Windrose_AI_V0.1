-- Row-level security (MASTER-FR-001): policy tenant_id = current_setting('app.tenant_id')::uuid.
-- The service sets app.tenant_id per request from the verified JWT.
-- NULLIF guards the cast when the GUC is unset (fails closed: no rows).
--
-- The `actions` table is the global, code-defined platform catalog and carries
-- no tenant data, so it has no RLS. `roles` rows with tenant_id IS NULL are
-- the shared system roles, readable by every tenant.
--
-- projection_dirty and outbox additionally allow the recompute/outbox workers
-- (app.worker = 'on', set only by service worker code) to claim rows across
-- tenants; both are internal queues never exposed through the API.

ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workspaces
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE groups ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON groups
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE members ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON members
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE workspace_groups ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workspace_groups
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE roles ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON roles
    USING (tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

-- role_actions has no tenant column; guard via the parent role's visibility.
ALTER TABLE role_actions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON role_actions
    USING (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_id))
    WITH CHECK (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_id));

ALTER TABLE group_roles ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON group_roles
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE content_grants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON content_grants
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

ALTER TABLE projection_dirty ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON projection_dirty
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
           OR current_setting('app.worker', true) = 'on')
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
           OR current_setting('app.worker', true) = 'on');

ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON outbox
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
           OR current_setting('app.worker', true) = 'on')
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
           OR current_setting('app.worker', true) = 'on');

ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON idempotency_keys
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
