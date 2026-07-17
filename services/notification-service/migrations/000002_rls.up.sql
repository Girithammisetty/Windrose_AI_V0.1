-- Row-level security (MASTER-FR-001): tenant-scoped tables get the policy
-- tenant_id = current_setting('app.tenant_id')::uuid. The service sets
-- app.tenant_id per transaction from the verified JWT — never from request
-- input (MASTER-FR-002). FORCE binds the policy to the table owner too, so even
-- an owner/superuser-owned session (migrations, tests) is constrained; the
-- shipped app role is additionally a NON-owner NOSUPERUSER role (000003).

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'subscription_rules','user_preferences','notifications',
    'webhook_endpoints','suppressions','idempotency_keys'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      t);
  END LOOP;
END $$;

-- deliveries + digest_buffers: per-tenant, plus a platform bypass so the retry
-- sweeper and digest-flush sweeper (durable Postgres workers) can read due rows
-- across tenants (they re-pin app.tenant_id before any write).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['deliveries','digest_buffers']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      t);
    EXECUTE format(
      'CREATE POLICY platform_sweep ON %I USING (current_setting(''app.role'', true) = ''platform'') WITH CHECK (true)',
      t);
  END LOOP;
END $$;

-- templates: platform-default rows (tenant_id IS NULL) are readable by every
-- tenant; per-tenant overrides only by that tenant (NOTIF-FR-041 resolution
-- order tenant → platform). Platform-default writes run under app.role=platform.
ALTER TABLE templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE templates FORCE ROW LEVEL SECURITY;
CREATE POLICY templates_read ON templates
  FOR SELECT
  USING (tenant_id IS NULL
         OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
         OR current_setting('app.role', true) = 'platform');
CREATE POLICY templates_write ON templates
  FOR ALL
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
         OR current_setting('app.role', true) = 'platform')
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
         OR current_setting('app.role', true) = 'platform');

-- outbox: per-tenant writes (from the mutation tx) + a platform bypass for the
-- relay that drains across tenants (MASTER-FR-034).
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON outbox
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (true);
CREATE POLICY outbox_platform ON outbox
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');
