-- Reverse the runtime app-role grants. The role is cluster-global; revoke
-- privileges but leave the role in place. Forward-only in practice (MASTER-FR-060).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM identity_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE USAGE, SELECT ON SEQUENCES FROM identity_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM identity_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM identity_app;
REVOKE USAGE ON SCHEMA public FROM identity_app;
