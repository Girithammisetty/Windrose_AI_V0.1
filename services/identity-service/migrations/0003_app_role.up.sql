-- Ship a NON-superuser, NON-owner runtime login role (tenant-isolation
-- remediation, cross-tenant RLS-bypass).
--
-- 0002 ENABLEd + FORCEd row-level security on every tenant table, but FORCE
-- (and even ENABLE) is silently ignored for a superuser or the table owner.
-- The shipped default DSN connected as `windrose` — the dev cluster SUPERUSER
-- with BYPASSRLS — so tenant_isolation was effectively OFF: a buggy/compromised
-- query could read another tenant's users/invitations/etc.
--
-- Fix: the runtime pool now logs in as `identity_app`, created here with
-- NOSUPERUSER NOBYPASSRLS. It holds DML only (no ownership), so FORCE RLS binds
-- it and the tenant_isolation / platform policies from 0002 are enforced.
-- Migrations keep running under the privileged role via MIGRATE_DATABASE_URL.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'identity_app') THEN
    ALTER ROLE identity_app WITH LOGIN PASSWORD 'identity_app' NOSUPERUSER NOBYPASSRLS;
  ELSE
    CREATE ROLE identity_app WITH LOGIN PASSWORD 'identity_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO identity_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO identity_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO identity_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO identity_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO identity_app;
