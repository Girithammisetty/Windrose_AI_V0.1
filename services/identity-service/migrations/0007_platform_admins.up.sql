-- First-class cross-tenant PLATFORM ADMINS (IDN). Platform-scoped, RLS-EXEMPT —
-- intentionally cross-tenant, like the tenants/cells/signing_keys registries.
-- This is NOT the per-tenant rbac "Admin" role: a user matched here (by sub or
-- email) is a human platform operator and gets the platform scopes +
-- platform_admin claim injected into their JWT at login (see token_oidc.go).
CREATE TABLE platform_admins (
    id         uuid PRIMARY KEY,
    user_sub   text,
    email      text NOT NULL UNIQUE,
    granted_by text NOT NULL DEFAULT '',
    granted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX platform_admins_sub_idx ON platform_admins (user_sub);

-- No ENABLE/FORCE ROW LEVEL SECURITY here: the registry is deliberately global.
-- DML grants to identity_app are covered by 0003's ALTER DEFAULT PRIVILEGES.
