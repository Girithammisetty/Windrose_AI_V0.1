-- Shipped non-owner runtime role (systemic rule): the service connects at
-- runtime as usage_app — a NOSUPERUSER NOBYPASSRLS role that is NOT the schema
-- owner — so Postgres RLS actually binds it (superusers/owners with BYPASSRLS
-- would silently defeat tenant isolation). Migrations run as the owner
-- (windrose/postgres) which creates this role and grants it CRUD; the default
-- DATABASE_URL in cmd/server points at usage_app, never at the owner.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'usage_app') THEN
    CREATE ROLE usage_app LOGIN PASSWORD 'usage_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO usage_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO usage_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO usage_app;

-- Future tables/sequences created by later migrations inherit the grant.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO usage_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO usage_app;
