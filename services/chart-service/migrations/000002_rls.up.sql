-- Row-level security (MASTER-FR-001) + a shipped NON-owner, NON-superuser
-- runtime role.
--
-- Two-part hardening (both non-negotiable):
--   1. Every tenant table gets ENABLE + FORCE ROW LEVEL SECURITY. FORCE binds
--      the policy even for the table owner, so an owner-connected session is
--      constrained too.
--   2. The service's SHIPPED default DSN connects as `chart_app`, a role
--      created here with NOSUPERUSER NOBYPASSRLS. Superusers and BYPASSRLS
--      roles silently ignore RLS; a superuser default DSN would fake out the
--      isolation guarantee. The runtime pool therefore never connects as the
--      migration owner (`windrose`/`postgres`).
--
-- The policy is `tenant_id = current_setting('app.tenant_id')::uuid`; the
-- service sets app.tenant_id per transaction from the verified JWT, never from
-- request input (MASTER-FR-002).

-- 1. tenant-scoped tables.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'dashboards','charts','chart_sources','chart_links',
    'documentations','operations','idempotency_keys'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I '
      || 'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) '
      || 'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      t);
  END LOOP;
END $$;

-- 2. outbox: tenant writes are pinned, but the relay reads across tenants under
-- app.role = 'platform'.
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON outbox
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY outbox_platform ON outbox
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');

-- 3. shipped non-owner runtime role. Idempotent: CREATE ROLE errors if it
-- already exists (roles are cluster-global), so guard it.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chart_app') THEN
    CREATE ROLE chart_app WITH LOGIN PASSWORD 'chart_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO chart_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO chart_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO chart_app;
-- Future tables/sequences (defensive; this service creates all objects up-front).
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO chart_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO chart_app;
