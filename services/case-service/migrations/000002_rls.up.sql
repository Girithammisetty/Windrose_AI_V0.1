-- Row-level security (MASTER-FR-001): tenant-scoped tables carry the policy
-- tenant_id = current_setting('app.tenant_id')::uuid. The service sets
-- app.tenant_id per transaction from the verified JWT — never from request
-- input (MASTER-FR-002). FORCE binds the table owner too so the test
-- container's superuser-owned sessions are also constrained (AC-13).

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'case_sequences','dispositions','case_fields','sla_policies','cases',
    'case_events','case_comments','sla_timers','applied_proposals',
    'operations','idempotency_keys'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      t);
  END LOOP;
END $$;

-- The outbox relay and the SLA sweep worker read across tenants under the
-- platform role; per-tenant mutations still pin app.tenant_id.
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON outbox
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (true);
CREATE POLICY outbox_platform ON outbox
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');

-- The SLA sweep worker scans sla_timers across tenants under the platform role.
CREATE POLICY sla_timers_platform ON sla_timers
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');
