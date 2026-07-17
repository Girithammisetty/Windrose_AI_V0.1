-- Ship a NON-superuser, NON-owner runtime login role (tenant-isolation
-- remediation, cross-tenant RLS-bypass).
--
-- 000002 ENABLEd + FORCEd row-level security on the tenant/platform tables, but
-- FORCE (and even ENABLE) is silently ignored for a superuser or the table
-- owner. The shipped default DSN connected as `windrose` — the dev cluster
-- SUPERUSER with BYPASSRLS — so tenant_isolation was effectively OFF.
--
-- Fix: both the registry and gateway runtime pools now log in as `toolplane_app`
-- (NOSUPERUSER NOBYPASSRLS, DML only), so FORCE RLS binds them and the
-- tenant_isolation / platform_admin / tenant_read policies from 000002 are
-- enforced. Migrations keep running privileged via MIGRATE_DATABASE_URL.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'toolplane_app') THEN
    ALTER ROLE toolplane_app WITH LOGIN PASSWORD 'toolplane_app' NOSUPERUSER NOBYPASSRLS;
  ELSE
    CREATE ROLE toolplane_app WITH LOGIN PASSWORD 'toolplane_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO toolplane_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO toolplane_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO toolplane_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO toolplane_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO toolplane_app;
