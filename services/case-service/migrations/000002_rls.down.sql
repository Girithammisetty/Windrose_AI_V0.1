DROP POLICY IF EXISTS sla_timers_platform ON sla_timers;
DROP POLICY IF EXISTS outbox_platform ON outbox;
DROP POLICY IF EXISTS outbox_tenant ON outbox;
ALTER TABLE outbox NO FORCE ROW LEVEL SECURITY;
ALTER TABLE outbox DISABLE ROW LEVEL SECURITY;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'case_sequences','dispositions','case_fields','sla_policies','cases',
    'case_events','case_comments','sla_timers','applied_proposals',
    'operations','idempotency_keys'
  ]
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
