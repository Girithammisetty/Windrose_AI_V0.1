-- Row-level security (MASTER-FR-001). Policy: tenant_id = app.tenant_id, set
-- per-transaction from the verified JWT (MASTER-FR-002) — never from request
-- input. FORCE ROW LEVEL SECURITY binds the table OWNER too, so even the
-- migration/superuser role is constrained (the shipped runtime role audit_rw is
-- a non-owner and is constrained by ENABLE regardless). This closes the
-- cross-tenant surface below the application.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['chain_heads','export_manifests','async_jobs','dlq_redrives']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      t);
    -- Platform-scoped read/write for cross-tenant maintenance paths (export
    -- scheduler, weekly self-verification) that set app.role=platform.
    EXECUTE format(
      'CREATE POLICY platform_access ON %I USING (current_setting(''app.role'', true) = ''platform'') WITH CHECK (current_setting(''app.role'', true) = ''platform'')',
      t);
  END LOOP;
END $$;
