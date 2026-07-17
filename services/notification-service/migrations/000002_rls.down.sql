DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['subscription_rules','user_preferences','notifications','webhook_endpoints','suppressions','idempotency_keys','deliveries','digest_buffers','templates','outbox']
  LOOP EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t); END LOOP;
END $$;
