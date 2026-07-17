-- Ship a NON-superuser, NON-owner runtime login role (tenant-isolation
-- remediation, FINDING task_971cc66f).
--
-- 000002 ENABLEd + FORCEd row-level security on every tenant table, but FORCE
-- (and even ENABLE) is silently ignored for a superuser or the table owner.
-- The shipped default DSN connected as `windrose` — a SUPERUSER with BYPASSRLS
-- (POSTGRES_USER of the dev cluster) — so tenant_isolation was effectively OFF:
-- a buggy/compromised query could read another tenant's cases.
--
-- Fix: the runtime pool now logs in as `case_app`, created here with
-- NOSUPERUSER NOBYPASSRLS. It holds DML only (no ownership), so FORCE RLS binds
-- it and the `tenant_isolation` / platform policies from 000002 are enforced.
-- Migrations keep running under the privileged role via MIGRATE_DATABASE_URL.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'case_app') THEN
    ALTER ROLE case_app WITH LOGIN PASSWORD 'case_app' NOSUPERUSER NOBYPASSRLS;
  ELSE
    CREATE ROLE case_app WITH LOGIN PASSWORD 'case_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO case_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO case_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO case_app;
-- Defensive: any object added by a later migration is grantable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO case_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO case_app;
