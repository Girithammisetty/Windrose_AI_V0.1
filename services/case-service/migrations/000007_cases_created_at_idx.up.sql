-- B5 (scalability audit): the full-tenant reindex reads live case ids ordered
-- by created_at (keyset pagination) under RLS's tenant_id equality filter.
-- Without a supporting index this is a per-tenant seq scan + sort that gets
-- worse as a tenant's case history grows toward millions of rows.
CREATE INDEX cases_tenant_created_idx ON cases (tenant_id, created_at, id)
    WHERE deleted_at IS NULL;
