DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'dashboards','charts','chart_sources','chart_links',
    'documentations','operations','idempotency_keys'
  ]
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
DROP POLICY IF EXISTS outbox_tenant ON outbox;
DROP POLICY IF EXISTS outbox_platform ON outbox;
ALTER TABLE outbox DISABLE ROW LEVEL SECURITY;
-- chart_app role intentionally left in place (cluster-global; other DBs may use it).
