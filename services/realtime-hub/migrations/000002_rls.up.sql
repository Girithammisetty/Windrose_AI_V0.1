-- Row-level security on the tenant-scoped audit table (MASTER-FR-001). The
-- service sets app.tenant_id from the verified JWT before every write.
-- routing_rules is global config (no tenant column), so it is not RLS-scoped.

ALTER TABLE stream_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE stream_tickets FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON stream_tickets
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);
