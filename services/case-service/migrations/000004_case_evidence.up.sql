-- Case evidence attachments (task #77): a case can hold uploaded files
-- (PDF/image/report/photo) as governed, tenant-isolated evidence. The bytes
-- live in object storage (MinIO); this table is the pointer + metadata.
-- Row-level security mirrors the other case content tables (000002).
CREATE TABLE case_evidence (
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL,
    workspace_id  UUID NOT NULL,
    case_id       UUID NOT NULL,
    filename      TEXT NOT NULL,
    content_type  TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL,
    storage_key   TEXT NOT NULL,
    uploaded_by   TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX case_evidence_case_idx ON case_evidence (tenant_id, case_id, created_at);

-- Tenant isolation (MASTER-FR-001), same policy shape as 000002_rls.
ALTER TABLE case_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE case_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON case_evidence
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

-- Grant the non-superuser runtime role (000003) DML on the new table. Guarded
-- so a deploy that never created case_app (default single-role dev) still
-- migrates cleanly; ALTER DEFAULT PRIVILEGES from 000003 also covers this.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'case_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON case_evidence TO case_app;
  END IF;
END $$;
