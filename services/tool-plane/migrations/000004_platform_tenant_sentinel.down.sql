DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tools','tool_versions','mcp_backends','byo_submissions','kill_switches','tool_health_hourly'] LOOP
    EXECUTE format(
      'UPDATE %I SET tenant_id = ''00000000-0000-0000-0000-000000000000''::uuid WHERE tenant_id = ''00000000-0000-7000-8000-000000000001''::uuid',
      t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_read ON %I', t);
    EXECUTE format(
      'CREATE POLICY tenant_read ON %I FOR SELECT USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid OR tenant_id = ''00000000-0000-0000-0000-000000000000''::uuid)',
      t);
  END LOOP;
END $$;

UPDATE outbox SET tenant_id = '00000000-0000-0000-0000-000000000000'::uuid
  WHERE tenant_id = '00000000-0000-7000-8000-000000000001'::uuid;
