DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tenant_tool_settings','invocation_log','idempotency_keys','tools','tool_versions','mcp_backends','byo_submissions','kill_switches','tool_health_hourly','outbox'] LOOP
    EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
