-- Row-level security (MASTER-FR-001): tenant-scoped tables get the policy
-- tenant_id = current_setting('app.tenant_id')::uuid. The service sets
-- app.tenant_id per transaction from the verified JWT — never from request
-- input (MASTER-FR-002). FORCE binds the policy on the table owner too, so
-- superuser-owned sessions in tests are also constrained, and the shipped
-- non-owner app role can never escape it.
--
-- Platform-operator endpoints (reconciliation, rate cards, cross-tenant
-- reports) run under app.role='platform' (USG-FR §4 bypass, audited).
-- The `meters` catalog is global (no tenant_id) so it carries no RLS.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'usage_raw','usage_hourly','usage_daily','usage_monthly',
    'budgets','budget_states','anomalies','adjustments','idempotency_keys'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I '
      'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid '
      '   OR current_setting(''app.role'', true) = ''platform'') '
      'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid '
      '   OR current_setting(''app.role'', true) = ''platform'')',
      t);
  END LOOP;
END $$;

-- rate_cards / rate_card_items / reconciliations are platform-managed but
-- rate_cards can be tenant-scoped (per-tenant overrides). Tenants may read
-- their own cards + the default (tenant_id IS NULL); platform manages all.
ALTER TABLE rate_cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_cards FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON rate_cards
  USING (tenant_id IS NULL
         OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
         OR current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');

ALTER TABLE rate_card_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_card_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON rate_card_items
  USING (EXISTS (SELECT 1 FROM rate_cards rc WHERE rc.id = rate_card_id))
  WITH CHECK (current_setting('app.role', true) = 'platform');

ALTER TABLE reconciliations ENABLE ROW LEVEL SECURITY;
ALTER TABLE reconciliations FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_only ON reconciliations
  USING (current_setting('app.role', true) = 'platform')
  WITH CHECK (current_setting('app.role', true) = 'platform');

-- Outbox: tenant writes its own rows within a request txn; the relay reads
-- across tenants under app.role='platform'.
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_tenant ON outbox
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
         OR current_setting('app.role', true) = 'platform')
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
         OR current_setting('app.role', true) = 'platform');
