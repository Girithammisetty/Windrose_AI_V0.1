-- Row-level security (MASTER-FR-001): tenant-scoped tables get the policy
-- tenant_id = current_setting('app.tenant_id')::uuid. The service sets
-- app.tenant_id per transaction from the verified JWT — never from request
-- input (MASTER-FR-002). FORCE makes the policy bind the table owner too,
-- so the test container's superuser-owned sessions are also constrained
-- (same style as identity-service/rbac-service).

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['saved_queries','saved_query_versions','executions','tenant_query_limits','idempotency_keys']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      t);
  END LOOP;
END $$;

-- Broker workers (executor goroutines finishing an execution, the GC and
-- the suspend consumer) run under app.role=worker with an explicit tenant
-- pin as well, so no cross-tenant surface opens up: worker paths still set
-- app.tenant_id. Only the outbox relay reads across tenants.
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON outbox
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (true);
CREATE POLICY outbox_platform ON outbox
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');
