-- FORCE row-level security on every tenant table + ship a NON-superuser,
-- NON-owner runtime login role (tenant-isolation remediation, cross-tenant
-- RLS-bypass).
--
-- 000002 only ENABLEd RLS (no FORCE). ENABLE/FORCE is silently ignored for a
-- superuser or the table owner, and the shipped default DSN connected as
-- `windrose` — the dev cluster SUPERUSER with BYPASSRLS — so tenant_isolation
-- was effectively OFF: a buggy/compromised query could read another tenant's
-- workspaces/groups/members/roles/grants.
--
-- Fix (both parts required):
--   1. FORCE ROW LEVEL SECURITY on every tenant table so the owner is bound too.
--   2. Create `rbac_app` (NOSUPERUSER NOBYPASSRLS, DML only) and point the
--      runtime pool at it. Migrations keep running privileged via
--      MIGRATE_DATABASE_URL.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'workspaces','groups','members','workspace_groups','roles','role_actions',
    'group_roles','content_grants','projection_dirty','outbox','idempotency_keys'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rbac_app') THEN
    ALTER ROLE rbac_app WITH LOGIN PASSWORD 'rbac_app' NOSUPERUSER NOBYPASSRLS;
  ELSE
    CREATE ROLE rbac_app WITH LOGIN PASSWORD 'rbac_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO rbac_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rbac_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rbac_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rbac_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO rbac_app;
