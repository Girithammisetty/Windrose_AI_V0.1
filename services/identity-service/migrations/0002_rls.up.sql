-- Row-level security (MASTER-FR-001): tenant-scoped tables get the policy
-- tenant_id = current_setting('app.tenant_id')::uuid. The service sets
-- app.tenant_id per transaction from the verified JWT — never from request
-- input. FORCE makes the policy apply to the table owner too, so the test
-- container's superuser-owned session is also constrained.
--
-- Platform-scoped tables (tenants, cells, provisioning_runs, signing_keys,
-- api_key_index, platform_versions) are RLS-exempt per BRD 01 §4.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','invitations','service_accounts','agent_principals','tenant_modules','idempotency_keys']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      t);
  END LOOP;
END $$;

-- Outbox: tenant-scoped writes ride the caller's transaction; the platform
-- poller reads all rows under app.role=platform.
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON outbox
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (true);
CREATE POLICY outbox_platform ON outbox
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');

-- Invitation acceptance is pre-auth (public activation link): the lookup by
-- token hash runs under app.role=platform in a dedicated store method.
CREATE POLICY invitations_platform ON invitations
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');

-- Same for the user row activated by that acceptance and for platform-level
-- workflows (provisioning seeds users before any tenant token exists).
CREATE POLICY users_platform ON users
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');
CREATE POLICY service_accounts_platform ON service_accounts
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');
CREATE POLICY agent_principals_platform ON agent_principals
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');
CREATE POLICY tenant_modules_platform ON tenant_modules
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');
