-- Ship a NON-superuser, NON-owner runtime login role (tenant-isolation
-- remediation, cross-tenant RLS-bypass).
--
-- 000002 ENABLEd + FORCEd row-level security on the tenant tables, but FORCE
-- (and even ENABLE) is silently ignored for a superuser or the table owner. The
-- shipped default DSN connected as `windrose` — the dev cluster SUPERUSER with
-- BYPASSRLS — so tenant_isolation was effectively OFF: a buggy/compromised query
-- could read another tenant's saved queries/executions.
--
-- Fix: the runtime pool now logs in as `query_app` (NOSUPERUSER NOBYPASSRLS, DML
-- only), so FORCE RLS binds it and tenant_isolation from 000002 is enforced.
-- Migrations keep running privileged via MIGRATE_DATABASE_URL.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'query_app') THEN
    ALTER ROLE query_app WITH LOGIN PASSWORD 'query_app' NOSUPERUSER NOBYPASSRLS;
  ELSE
    CREATE ROLE query_app WITH LOGIN PASSWORD 'query_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO query_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO query_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO query_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO query_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO query_app;
