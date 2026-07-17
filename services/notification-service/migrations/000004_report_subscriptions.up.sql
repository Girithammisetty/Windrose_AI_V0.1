-- report_subscriptions: scheduled dashboard-report email digests (NOTIF-FR-060,
-- "Case Reports / Team Reports"). One row per subscription; each
-- enabled row backs exactly one real Temporal Schedule (cron) that fires
-- ReportWorkflow, which fetches live dashboard data from chart-service and
-- emails it via the same real email.Sender used by NOTIF-FR-030 digests.
CREATE TABLE report_subscriptions (
    id                 uuid PRIMARY KEY,
    tenant_id          uuid NOT NULL,
    workspace_id       uuid NOT NULL,
    dashboard_id       uuid NOT NULL,
    name               text NOT NULL,
    recipients         text[] NOT NULL,
    cadence            text NOT NULL,              -- 'daily' | 'weekly'
    send_hour          int  NOT NULL DEFAULT 8,     -- 0-23, local to timezone
    send_weekday       int,                         -- 0(Sun)-6(Sat), required for weekly
    timezone           text NOT NULL DEFAULT 'UTC',
    format             text NOT NULL DEFAULT 'html', -- 'html' | 'text'
    enabled            boolean NOT NULL DEFAULT true,
    temporal_schedule_id text NOT NULL DEFAULT '',   -- Temporal Schedule handle (blank until first sync)
    last_sent_at       timestamptz,
    last_status        text NOT NULL DEFAULT '',     -- '' | 'sent' | 'failed'
    last_error         text NOT NULL DEFAULT '',
    created_by         text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    deleted_at         timestamptz,
    CONSTRAINT chk_report_cadence CHECK (cadence IN ('daily','weekly')),
    CONSTRAINT chk_report_format CHECK (format IN ('html','text')),
    CONSTRAINT chk_report_hour CHECK (send_hour BETWEEN 0 AND 23),
    CONSTRAINT chk_report_weekday CHECK (send_weekday IS NULL OR send_weekday BETWEEN 0 AND 6),
    CONSTRAINT chk_report_recipients CHECK (array_length(recipients, 1) BETWEEN 1 AND 50)
);
CREATE INDEX ix_report_subs_tenant ON report_subscriptions (tenant_id, enabled);
CREATE INDEX ix_report_subs_dashboard ON report_subscriptions (tenant_id, dashboard_id);

ALTER TABLE report_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_subscriptions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON report_subscriptions
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
