/** notification-service REST client (NOTIF-FR-060). Backs scheduled dashboard
 * report subscriptions ("Team Reports"): CRUD + a manual trigger, on top of a
 * real Temporal Schedule per subscription. Pure passthrough — the caller's JWT
 * is forwarded verbatim and notification-service enforces every
 * `notification.report.*` action guard; the BFF reshapes snake_case to camelCase
 * only (BFF-FR-003/010/011), same as every other client in this directory. */
import { ServiceClient } from "./base.js";
import { unwrap, type Page } from "./types.js";

export interface ReportSubscriptionDTO {
  id: string;
  tenant_id?: string;
  workspace_id: string;
  dashboard_id: string;
  name: string;
  recipients: string[];
  cadence: string; // daily | weekly
  send_hour: number;
  send_weekday?: number | null;
  timezone: string;
  format: string; // html | text
  enabled: boolean;
  temporal_schedule_id?: string;
  last_sent_at?: string | null;
  last_status?: string;
  last_error?: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface CreateReportSubscriptionBody {
  dashboard_id: string;
  workspace_id: string;
  name: string;
  recipients: string[];
  cadence: string;
  send_hour?: number;
  send_weekday?: number | null;
  timezone?: string;
  format?: string;
  enabled?: boolean;
}

export interface UpdateReportSubscriptionBody {
  name?: string;
  recipients?: string[];
  cadence?: string;
  send_hour?: number;
  send_weekday?: number | null;
  timezone?: string;
  format?: string;
  enabled?: boolean;
}

// ============================================================================
// Tier 2b: in-app inbox, preferences, subscription rules, webhooks, templates,
// admin stats/suppressions (NOTIF-FR-010/012/020/022/024/040/051). Shapes
// mirror internal/domain/types.go + the handlers in internal/api/.
// ============================================================================

export interface NotificationDTO {
  id: string;
  tenant_id?: string;
  user_id?: string;
  event_id?: string;
  event_type: string;
  severity_class?: string;
  title: string;
  body?: string;
  resource_urn?: string;
  deep_link?: string;
  matched_rules?: string[];
  read_at?: string | null;
  created_at: string;
}

export interface NotificationPreferencesDTO {
  tenant_id?: string;
  user_id?: string;
  channel_overrides: Record<string, string[]>;
  mutes?: { event_types?: string[]; resource_urns?: string[] };
  quiet_hours?: { tz: string; start: string; end: string } | null;
  digest_config: Record<string, string>;
  updated_at?: string;
}

export interface PutPreferencesBody {
  channel_overrides?: Record<string, string[]>;
  mutes?: { event_types?: string[]; resource_urns?: string[] };
  quiet_hours?: { tz: string; start: string; end: string } | null;
  digest_config?: Record<string, string>;
}

export interface NotificationRuleDTO {
  id: string;
  tenant_id?: string;
  scope: string;
  subject_type: string;
  subject_id: string;
  event_types: string[];
  resource_filter?: { resource_urn_prefix?: string; attrs?: Record<string, string[]> };
  channels: string[];
  digest_enabled: boolean;
  digest_window: string;
  active: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface NotificationRuleBody {
  scope?: string;
  subject_type?: string;
  subject_id?: string;
  event_types?: string[];
  resource_filter?: { resource_urn_prefix?: string; attrs?: Record<string, string[]> };
  channels?: string[];
  digest_enabled?: boolean;
  digest_window?: string;
  active?: boolean;
}

export interface WebhookSecretDTO {
  version: number;
  secret: string;
  created_at: string;
  expires_at?: string | null;
}

export interface WebhookEndpointDTO {
  id: string;
  tenant_id?: string;
  url: string;
  event_types: string[];
  secrets: WebhookSecretDTO[];
  active: boolean;
  verified_at?: string | null;
  circuit_state?: string;
  circuit_opened_at?: string | null;
  consecutive_failures?: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface WebhookBody {
  url?: string;
  event_types?: string[];
  active?: boolean;
}

export interface WebhookDeliveryDTO {
  id: string;
  tenant_id?: string;
  notification_id?: string | null;
  webhook_endpoint_id?: string | null;
  event_id: string;
  recipient?: string;
  channel?: string;
  provider?: string;
  status: string;
  provider_msg_id?: string;
  attempts: number;
  last_error?: string;
  next_retry_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationTemplateDTO {
  id: string;
  tenant_id?: string | null;
  key: string;
  channel: string;
  locale: string;
  version: number;
  subject_tpl: string;
  body_html_tpl: string;
  body_text_tpl: string;
  status: string;
  published_at?: string | null;
  created_by: string;
  created_at: string;
}

export interface CreateTemplateBody {
  key: string;
  channel: string;
  locale?: string;
  subject_tpl?: string;
  body_html_tpl?: string;
  body_text_tpl?: string;
}

export interface TemplatePreviewDTO {
  subject: string;
  html: string;
  text: string;
}

export interface DeliveryStatsDTO {
  window: string;
  by_channel: unknown;
}

export interface SuppressionDTO {
  id: string;
  tenant_id?: string;
  email_hash: string;
  reason: string;
  created_at: string;
  cleared_at?: string | null;
}

export class NotificationClient {
  constructor(private readonly http: ServiceClient) {}

  // ---- Tier 2b: inbox (NOTIF-FR-020) ---------------------------------------

  notifications(unread: boolean | undefined, limit: number, cursor?: string): Promise<Page<NotificationDTO>> {
    return this.http.get<Page<NotificationDTO>>("/api/v1/notifications", {
      query: { "filter[unread]": unread ? "true" : undefined, limit, cursor },
    });
  }

  async unreadCount(): Promise<number> {
    const r = await this.http.get<{ data: { unread: number } }>("/api/v1/notifications/unread-count");
    return r.data?.unread ?? 0;
  }

  /** POST /notifications/{id}/read|unread — 204 on success. */
  async setNotificationRead(id: string, read: boolean): Promise<void> {
    await this.http.post<void>(
      `/api/v1/notifications/${encodeURIComponent(id)}/${read ? "read" : "unread"}`,
    );
  }

  async markAllRead(): Promise<number> {
    const r = await this.http.post<{ data: { marked: number } }>("/api/v1/notifications/mark-all-read");
    return r.data?.marked ?? 0;
  }

  // ---- Tier 2b: per-user preferences (NOTIF-FR-012) ------------------------

  async preferences(): Promise<NotificationPreferencesDTO> {
    const r = await this.http.get<{ data: NotificationPreferencesDTO } | NotificationPreferencesDTO>("/api/v1/preferences");
    return unwrap<NotificationPreferencesDTO>(r);
  }

  async putPreferences(body: PutPreferencesBody, idempotencyKey?: string): Promise<NotificationPreferencesDTO> {
    const r = await this.http.put<{ data: NotificationPreferencesDTO } | NotificationPreferencesDTO>("/api/v1/preferences", {
      body,
      idempotencyKey,
    });
    return unwrap<NotificationPreferencesDTO>(r);
  }

  // ---- Tier 2b: subscription rules (NOTIF-FR-010) --------------------------

  rules(limit: number, cursor?: string): Promise<Page<NotificationRuleDTO>> {
    return this.http.get<Page<NotificationRuleDTO>>("/api/v1/rules", { query: { limit, cursor } });
  }

  async createRule(body: NotificationRuleBody, idempotencyKey?: string): Promise<NotificationRuleDTO> {
    const r = await this.http.post<{ data: NotificationRuleDTO } | NotificationRuleDTO>("/api/v1/rules", {
      body,
      idempotencyKey,
    });
    return unwrap<NotificationRuleDTO>(r);
  }

  async updateRule(id: string, body: NotificationRuleBody, idempotencyKey?: string): Promise<NotificationRuleDTO> {
    const r = await this.http.patch<{ data: NotificationRuleDTO } | NotificationRuleDTO>(
      `/api/v1/rules/${encodeURIComponent(id)}`,
      { body, idempotencyKey },
    );
    return unwrap<NotificationRuleDTO>(r);
  }

  async deleteRule(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/rules/${encodeURIComponent(id)}`);
  }

  // ---- Tier 2b: webhooks (NOTIF-FR-022/024) ---------------------------------

  webhooks(limit: number, cursor?: string): Promise<Page<WebhookEndpointDTO>> {
    return this.http.get<Page<WebhookEndpointDTO>>("/api/v1/webhooks", { query: { limit, cursor } });
  }

  async createWebhook(body: WebhookBody, idempotencyKey?: string): Promise<WebhookEndpointDTO> {
    const r = await this.http.post<{ data: WebhookEndpointDTO } | WebhookEndpointDTO>("/api/v1/webhooks", {
      body,
      idempotencyKey,
    });
    return unwrap<WebhookEndpointDTO>(r);
  }

  async updateWebhook(id: string, body: WebhookBody, idempotencyKey?: string): Promise<WebhookEndpointDTO> {
    const r = await this.http.patch<{ data: WebhookEndpointDTO } | WebhookEndpointDTO>(
      `/api/v1/webhooks/${encodeURIComponent(id)}`,
      { body, idempotencyKey },
    );
    return unwrap<WebhookEndpointDTO>(r);
  }

  async deleteWebhook(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/webhooks/${encodeURIComponent(id)}`);
  }

  /** POST /webhooks/{id}/rotate-secret — the response carries BOTH secret
   * versions during the 24h overlap (NOTIF-FR-022 AC-6). */
  async rotateWebhookSecret(id: string, idempotencyKey?: string): Promise<WebhookEndpointDTO> {
    const r = await this.http.post<{ data: WebhookEndpointDTO } | WebhookEndpointDTO>(
      `/api/v1/webhooks/${encodeURIComponent(id)}/rotate-secret`,
      { idempotencyKey },
    );
    return unwrap<WebhookEndpointDTO>(r);
  }

  webhookDeliveries(id: string, limit: number, cursor?: string): Promise<Page<WebhookDeliveryDTO>> {
    return this.http.get<Page<WebhookDeliveryDTO>>(
      `/api/v1/webhooks/${encodeURIComponent(id)}/deliveries`,
      { query: { limit, cursor } },
    );
  }

  /** POST /webhooks/{id}/deliveries/{did}/redeliver — 202 {status: requeued}. */
  async redeliverWebhookDelivery(webhookId: string, deliveryId: string, idempotencyKey?: string): Promise<void> {
    await this.http.post<void>(
      `/api/v1/webhooks/${encodeURIComponent(webhookId)}/deliveries/${encodeURIComponent(deliveryId)}/redeliver`,
      { idempotencyKey },
    );
  }

  // ---- Tier 2b: templates (NOTIF-FR-040/041) --------------------------------

  async templates(key: string): Promise<NotificationTemplateDTO[]> {
    const r = await this.http.get<{ data: NotificationTemplateDTO[] }>("/api/v1/templates", {
      query: { "filter[key]": key },
    });
    return r.data ?? [];
  }

  async createTemplate(body: CreateTemplateBody, idempotencyKey?: string): Promise<NotificationTemplateDTO> {
    const r = await this.http.post<{ data: NotificationTemplateDTO } | NotificationTemplateDTO>("/api/v1/templates", {
      body,
      idempotencyKey,
    });
    return unwrap<NotificationTemplateDTO>(r);
  }

  async publishTemplate(key: string, templateId: string, idempotencyKey?: string): Promise<NotificationTemplateDTO> {
    const r = await this.http.post<{ data: NotificationTemplateDTO } | NotificationTemplateDTO>(
      `/api/v1/templates/${encodeURIComponent(key)}/publish`,
      { body: { template_id: templateId }, idempotencyKey },
    );
    return unwrap<NotificationTemplateDTO>(r);
  }

  async previewTemplate(
    key: string,
    body: { channel?: string; locale?: string; sample_event?: Record<string, unknown> },
  ): Promise<TemplatePreviewDTO> {
    const r = await this.http.post<{ data: TemplatePreviewDTO } | TemplatePreviewDTO>(
      `/api/v1/templates/${encodeURIComponent(key)}/preview`,
      { body },
    );
    return unwrap<TemplatePreviewDTO>(r);
  }

  // ---- Tier 2b: ops (NOTIF-FR-051) ------------------------------------------

  async deliveryStats(window?: string): Promise<DeliveryStatsDTO> {
    const r = await this.http.get<{ data: DeliveryStatsDTO } | DeliveryStatsDTO>("/api/v1/admin/stats", {
      query: { window },
    });
    return unwrap<DeliveryStatsDTO>(r);
  }

  async suppressions(): Promise<SuppressionDTO[]> {
    const r = await this.http.get<{ data: SuppressionDTO[] }>("/api/v1/admin/suppressions");
    return r.data ?? [];
  }

  /** DELETE /admin/suppressions?email_hash=… — 204 on success. */
  async clearSuppression(emailHash: string): Promise<void> {
    await this.http.delete<void>("/api/v1/admin/suppressions", { query: { email_hash: emailHash } });
  }

  reportSubscriptions(dashboardId: string | undefined, limit: number, cursor?: string): Promise<Page<ReportSubscriptionDTO>> {
    return this.http.get<Page<ReportSubscriptionDTO>>("/api/v1/reports", {
      query: { dashboard_id: dashboardId, limit, cursor },
    });
  }

  async reportSubscription(id: string): Promise<ReportSubscriptionDTO> {
    const r = await this.http.get<{ data: ReportSubscriptionDTO } | ReportSubscriptionDTO>(
      `/api/v1/reports/${encodeURIComponent(id)}`,
    );
    return unwrap<ReportSubscriptionDTO>(r);
  }

  async createReportSubscription(body: CreateReportSubscriptionBody, idempotencyKey?: string): Promise<ReportSubscriptionDTO> {
    const r = await this.http.post<{ data: ReportSubscriptionDTO } | ReportSubscriptionDTO>("/api/v1/reports", {
      body,
      idempotencyKey,
    });
    return unwrap<ReportSubscriptionDTO>(r);
  }

  async updateReportSubscription(
    id: string,
    body: UpdateReportSubscriptionBody,
    idempotencyKey?: string,
  ): Promise<ReportSubscriptionDTO> {
    const r = await this.http.patch<{ data: ReportSubscriptionDTO } | ReportSubscriptionDTO>(
      `/api/v1/reports/${encodeURIComponent(id)}`,
      { body, idempotencyKey },
    );
    return unwrap<ReportSubscriptionDTO>(r);
  }

  async deleteReportSubscription(id: string): Promise<void> {
    await this.http.delete<void>(`/api/v1/reports/${encodeURIComponent(id)}`);
  }

  /** POST /reports/{id}/trigger — fire one immediate real Temporal run outside
   * the cron cadence ("send now" / live verification). */
  async triggerReportSubscription(id: string): Promise<void> {
    await this.http.post<void>(`/api/v1/reports/${encodeURIComponent(id)}/trigger`);
  }
}
