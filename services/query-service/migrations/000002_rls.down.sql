DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['saved_queries','saved_query_versions','executions','tenant_query_limits','idempotency_keys']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
DROP POLICY IF EXISTS outbox_tenant ON outbox;
DROP POLICY IF EXISTS outbox_platform ON outbox;
ALTER TABLE outbox DISABLE ROW LEVEL SECURITY;
