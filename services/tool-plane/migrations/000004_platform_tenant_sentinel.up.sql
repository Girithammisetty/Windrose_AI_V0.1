-- Fix domain.PlatformTenant: it was uuid.Nil (00000000-0000-0000-0000-000000000000),
-- which libs/go-common/event.Validate (MASTER-FR-031/041, mirrored by
-- audit-service's consumption-side ValidateEnvelope) rejects as a missing
-- tenant_id. Every platform-scoped tool.events.v1 lifecycle event (tool
-- registered/version_published/deprecated/retired/killed/unkilled/
-- sla_breached/quarantined) was therefore failing envelope conformance and
-- would DLQ at audit-service as ENVELOPE_INVALID.
--
-- Fix: adopt the same reserved platform tenant uuid ai-gateway already uses
-- (services/ai-gateway/app/config.py PLATFORM_TENANT_ID) so "the reserved
-- platform tenant" (BRD 12 §services/ai-gateway, BRD 13 §4) is one identity
-- platform-wide, not a per-service invention. Re-point the RLS tenant_read
-- policy literal on every platform-scoped catalog table (000002_rls.up.sql)
-- and migrate any rows already persisted under the old all-zero sentinel.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tools','tool_versions','mcp_backends','byo_submissions','kill_switches','tool_health_hourly'] LOOP
    EXECUTE format(
      'UPDATE %I SET tenant_id = ''00000000-0000-7000-8000-000000000001''::uuid WHERE tenant_id = ''00000000-0000-0000-0000-000000000000''::uuid',
      t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_read ON %I', t);
    EXECUTE format(
      'CREATE POLICY tenant_read ON %I FOR SELECT USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid OR tenant_id = ''00000000-0000-7000-8000-000000000001''::uuid)',
      t);
  END LOOP;
END $$;

-- Outbox rows aren't policy-scoped by this literal (outbox_tenant/outbox_platform
-- key off app.role, not a fixed tenant_id), but any already-queued
-- platform-scoped envelope needs the same data migration so the relay
-- publishes it under the new sentinel.
UPDATE outbox SET tenant_id = '00000000-0000-7000-8000-000000000001'::uuid
  WHERE tenant_id = '00000000-0000-0000-0000-000000000000'::uuid;
