-- Reverse the runtime app-role grants and drop FORCE (leave ENABLE + policies).
-- The role is cluster-global; revoke privileges but leave the role in place.
-- Forward-only in practice (MASTER-FR-060).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM rbac_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE USAGE, SELECT ON SEQUENCES FROM rbac_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM rbac_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM rbac_app;
REVOKE USAGE ON SCHEMA public FROM rbac_app;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'workspaces','groups','members','workspace_groups','roles','role_actions',
    'group_roles','content_grants','projection_dirty','outbox','idempotency_keys'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
