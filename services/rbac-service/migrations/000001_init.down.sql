-- Migrations are forward-only in CI (MASTER-FR-060); down provided for local dev only.
DROP SEQUENCE IF EXISTS projection_version_seq;
DROP TABLE IF EXISTS idempotency_keys;
DROP TABLE IF EXISTS outbox;
DROP TABLE IF EXISTS projection_dirty;
DROP TABLE IF EXISTS content_grants;
DROP TABLE IF EXISTS group_roles;
DROP TABLE IF EXISTS role_actions;
DROP TABLE IF EXISTS actions;
DROP TABLE IF EXISTS roles;
DROP TABLE IF EXISTS workspace_groups;
DROP TABLE IF EXISTS members;
DROP TABLE IF EXISTS groups;
DROP TABLE IF EXISTS workspaces;
