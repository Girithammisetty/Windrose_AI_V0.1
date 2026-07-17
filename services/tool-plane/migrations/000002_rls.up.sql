-- Row-level security (MASTER-FR-001). Tenant-scoped tables get the policy
-- tenant_id = current_setting('app.tenant_id')::uuid; the service sets
-- app.tenant_id per transaction from the verified JWT, never from request input
-- (MASTER-FR-002). FORCE binds the policy to the table owner too, so even a
-- superuser-owned session (test containers) is constrained. Platform-scoped
-- catalog tables additionally admit an app.role='platform' session for the
-- registry's cross-tenant catalog administration and the outbox relay.

-- Tenant-scoped tables: strict tenant isolation.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tenant_tool_settings','invocation_log','idempotency_keys'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      t);
  END LOOP;
END $$;

-- Platform-scoped catalog tables: the registry administers them under an
-- app.role='platform' session; a tenant session may still READ its own rows
-- (tenant_id match) so the gateway can resolve catalog state under RLS.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tools','tool_versions','mcp_backends','byo_submissions','kill_switches','tool_health_hourly'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY platform_admin ON %I USING (current_setting(''app.role'', true) = ''platform'') WITH CHECK (current_setting(''app.role'', true) = ''platform'')',
      t);
    EXECUTE format(
      'CREATE POLICY tenant_read ON %I FOR SELECT USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid OR tenant_id = ''00000000-0000-0000-0000-000000000000''::uuid)',
      t);
  END LOOP;
END $$;

-- Outbox: tenant sessions write their own rows (WITH CHECK true so the platform
-- relay may also read across tenants).
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON outbox
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (true);
CREATE POLICY outbox_platform ON outbox
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');
