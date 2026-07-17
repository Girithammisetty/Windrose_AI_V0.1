-- Ship a NON-superuser, NON-owner runtime login role (tenant-isolation
-- remediation, cross-tenant RLS-bypass).
--
-- 000002 ENABLEd + FORCEd row-level security on stream_tickets, but FORCE (and
-- even ENABLE) is silently ignored for a superuser or the table owner. The
-- shipped default DSN connected as `windrose` — the dev cluster SUPERUSER with
-- BYPASSRLS — so tenant_isolation was effectively OFF.
--
-- Fix: the runtime pool now logs in as `realtime_app` (NOSUPERUSER NOBYPASSRLS,
-- DML only), so FORCE RLS binds it and tenant_isolation is enforced. Migrations
-- keep running under the privileged role via MIGRATE_DATABASE_URL.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'realtime_app') THEN
    ALTER ROLE realtime_app WITH LOGIN PASSWORD 'realtime_app' NOSUPERUSER NOBYPASSRLS;
  ELSE
    CREATE ROLE realtime_app WITH LOGIN PASSWORD 'realtime_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO realtime_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO realtime_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO realtime_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO realtime_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO realtime_app;
