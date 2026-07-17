DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'usage_raw','usage_hourly','usage_daily','usage_monthly',
    'budgets','budget_states','anomalies','adjustments','idempotency_keys',
    'rate_cards','rate_card_items','reconciliations','outbox'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
