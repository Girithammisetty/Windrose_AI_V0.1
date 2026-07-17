-- Reverse the runtime app role grants. The role is cluster-global and may own
-- nothing; revoke privileges but leave the role in place (other databases in the
-- dev cluster may share it). Forward-only in practice (MASTER-FR-060).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM case_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE USAGE, SELECT ON SEQUENCES FROM case_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM case_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM case_app;
REVOKE USAGE ON SCHEMA public FROM case_app;
