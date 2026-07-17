-- Reverse the runtime app-role grants. The role is cluster-global; revoke
-- privileges but leave the role in place. Forward-only in practice (MASTER-FR-060).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM realtime_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE USAGE, SELECT ON SEQUENCES FROM realtime_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM realtime_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM realtime_app;
REVOKE USAGE ON SCHEMA public FROM realtime_app;
