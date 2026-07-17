DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_rw') THEN
    REVOKE SELECT, INSERT, UPDATE ON chain_heads, export_manifests, async_jobs, dlq_redrives FROM audit_rw;
    REVOKE USAGE ON SCHEMA public FROM audit_rw;
  END IF;
END $$;
