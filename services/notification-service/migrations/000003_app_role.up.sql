-- Non-owner, NOSUPERUSER, NOBYPASSRLS application role (systemic rule): the
-- shipped default DATABASE_URL connects as this role, NOT as the schema owner
-- (windrose). Because it is neither the table owner nor a superuser, the RLS
-- policies in 000002 actually bind it — a service that connected as the owner
-- could silently bypass tenant isolation. Migrations run under the owner DSN
-- (MIGRATE_DATABASE_URL); the runtime pool uses this role.
--
-- The dev password is a fixed local credential (compose/dev only); in a real
-- cell the role's password is provisioned from Vault. Role creation is guarded
-- so re-running migrations is idempotent.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'notif_app') THEN
    CREATE ROLE notif_app LOGIN PASSWORD 'notif_app_pw' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO notif_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO notif_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO notif_app;
-- Future tables/sequences created by the owner in this schema.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO notif_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO notif_app;
