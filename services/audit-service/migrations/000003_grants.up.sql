-- Least-privilege runtime role (systemic rule): the shipped default DSN connects
-- as audit_rw — a NON-owner, NON-superuser, NOBYPASSRLS role — NOT the owner
-- (windrose). Tables are owned by the migration role; audit_rw only holds the
-- DML it needs and is fully subject to RLS. The role itself is created in the Go
-- bootstrap (CREATE ROLE, outside a migration tx); this migration grants it the
-- minimal privileges. Guarded so migrations succeed even if the role is absent
-- in an environment that provisions it differently.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_rw') THEN
    GRANT USAGE ON SCHEMA public TO audit_rw;
    -- Append-only intent for events lives in ClickHouse; the PG metadata tables
    -- legitimately need INSERT/UPDATE (chain head advance, manifest seal, job
    -- status). No DELETE is granted.
    GRANT SELECT, INSERT, UPDATE ON chain_heads, export_manifests, async_jobs, dlq_redrives TO audit_rw;
  END IF;
END $$;
