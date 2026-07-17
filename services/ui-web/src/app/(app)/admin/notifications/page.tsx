"use client";
import { useMemo, useState } from "react";
import { Webhook as WebhookIcon, ListChecks, FileText, Activity, X, RefreshCcw } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardDescription, CardContent, Input, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useNotificationRules, useCreateNotificationRule, useUpdateNotificationRule, useDeleteNotificationRule,
  useNotificationWebhooks, useCreateNotificationWebhook, useUpdateNotificationWebhook, useDeleteNotificationWebhook,
  useRotateNotificationWebhookSecret, useNotificationWebhookDeliveries, useRedeliverNotificationWebhookDelivery,
  useNotificationTemplates, useCreateNotificationTemplate, usePublishNotificationTemplate, usePreviewNotificationTemplate,
  useNotificationDeliveryStats, useEmailSuppressions, useClearEmailSuppression,
} from "@/lib/graphql/hooks";
import type { NotificationRule, WebhookEndpoint, WebhookDelivery, NotificationTemplate, EmailSuppression } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";
import { formatLocal } from "@/lib/utils";

/**
 * Notification admin (Tier 2b, notification-service NOTIF-FR-010/022/024/
 * 040/051 via the bff): subscription rules, webhook endpoints (create with a
 * REAL challenge handshake, rotate-secret shown once, delivery history +
 * redeliver), template versions, and tenant delivery health.
 */
export default function AdminNotificationsPage() {
  return (
    <div>
      <PageHeader title={t("notifAdmin.title")} description={t("notifAdmin.subtitle")} />
      <div className="space-y-4">
        <RulesCard />
        <WebhooksCard />
        <div className="grid gap-4 lg:grid-cols-2">
          <TemplatesCard />
          <OpsCard />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subscription rules
// ---------------------------------------------------------------------------
function RulesCard() {
  const query = useNotificationRules();
  const create = useCreateNotificationRule();
  const update = useUpdateNotificationRule();
  const del = useDeleteNotificationRule();
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<NotificationRule | null>(null);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const columns: Column<NotificationRule>[] = [
    { id: "events", header: "Event types", cell: (r) => <span className="font-mono text-xs">{r.eventTypes.join(", ")}</span> },
    { id: "channels", header: "Channels", width: 150, cell: (r) => r.channels.join(", ") },
    { id: "scope", header: "Scope", width: 110, cell: (r) => r.scope },
    {
      id: "active", header: "Active", width: 90,
      cell: (r) => <Badge variant={r.active ? "default" : "warning"}>{r.active ? "active" : "paused"}</Badge>,
    },
    { id: "created", header: "Created", width: 160, cell: (r) => formatLocal(r.createdAt) },
    {
      id: "actions", header: "", width: 170,
      cell: (r) => (
        <span className="flex gap-1">
          <Can gate={FEATURE_GATES.updateNotificationRule}>
            <Button
              variant="ghost" size="sm" disabled={update.isPending}
              onClick={(e) => { e.stopPropagation(); update.mutate({ id: r.id, input: { eventTypes: r.eventTypes, channels: r.channels, active: !r.active } }); }}
            >
              {r.active ? "Pause" : "Resume"}
            </Button>
          </Can>
          <Can gate={FEATURE_GATES.deleteNotificationRule}>
            <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setConfirmDelete(r); }}>
              {t("action.delete")}
            </Button>
          </Can>
        </span>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm">
          <ListChecks className="size-4" aria-hidden /> {t("notifAdmin.rules.title")}
        </CardTitle>
        <Can gate={FEATURE_GATES.createNotificationRule}>
          <Button size="sm" onClick={() => setCreating((v) => !v)}>
            {creating ? t("action.cancel") : t("notifAdmin.rules.new")}
          </Button>
        </Can>
      </CardHeader>
      <CardContent className="space-y-3">
        {creating && (
          <NewRuleForm
            pending={create.isPending}
            error={create.error}
            onCreate={(input) => create.mutate(input, { onSuccess: () => setCreating(false) })}
          />
        )}
        <AsyncBoundary
          isLoading={query.isLoading} isError={query.isError} error={query.error}
          isEmpty={rows.length === 0} emptyTitle={t("notifAdmin.rules.empty")} onRetry={() => query.refetch()}
        >
          <DataTable ariaLabel={t("notifAdmin.rules.title")} rows={rows} columns={columns} rowId={(r) => r.id} />
        </AsyncBoundary>
      </CardContent>

      <ConfirmDialog
        open={confirmDelete !== null}
        onOpenChange={(o) => !o && setConfirmDelete(null)}
        title={t("notifAdmin.rules.confirmDelete.title")}
        description={t("notifAdmin.rules.confirmDelete.description")}
        confirmLabel={t("action.delete")}
        destructive
        onConfirm={() => { if (confirmDelete) del.mutate(confirmDelete.id); setConfirmDelete(null); }}
      />
    </Card>
  );
}

function NewRuleForm({
  onCreate, pending, error,
}: {
  onCreate: (input: { eventTypes: string[]; channels: string[]; digestEnabled?: boolean; digestWindow?: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [eventTypes, setEventTypes] = useState("");
  const [channels, setChannels] = useState("inapp");
  const [digest, setDigest] = useState(false);

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const ev = eventTypes.split(",").map((s) => s.trim()).filter(Boolean);
        const ch = channels.split(",").map((s) => s.trim()).filter(Boolean);
        if (ev.length === 0 || ch.length === 0) return;
        onCreate({ eventTypes: ev, channels: ch, digestEnabled: digest });
      }}
    >
      <label className="flex min-w-64 flex-1 flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Event types (comma-separated, e.g. case.assigned.v1)</span>
        <Input value={eventTypes} onChange={(e) => setEventTypes(e.target.value)} aria-label="Rule event types" className="h-8 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Channels (inapp, email, webhook)</span>
        <Input value={channels} onChange={(e) => setChannels(e.target.value)} aria-label="Rule channels" className="h-8 w-52 text-xs" />
      </label>
      <label className="flex items-center gap-1.5 pb-1.5 text-xs">
        <input type="checkbox" checked={digest} onChange={(e) => setDigest(e.target.checked)} aria-label="Digest enabled" />
        <span className="text-muted-foreground">Digest</span>
      </label>
      <Button type="submit" size="sm" disabled={pending}>{t("notifAdmin.rules.new")}</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Webhooks
// ---------------------------------------------------------------------------
function WebhooksCard() {
  const query = useNotificationWebhooks();
  const create = useCreateNotificationWebhook();
  const [creating, setCreating] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The create response is the ONLY place the v1 signing secret is surfaced.
  const [createdSecret, setCreatedSecret] = useState<{ url: string; secret: string } | null>(null);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const selected = rows.find((w) => w.id === selectedId) ?? null;

  const columns: Column<WebhookEndpoint>[] = [
    { id: "url", header: "URL", cell: (w) => <span className="font-mono text-xs">{w.url}</span> },
    { id: "events", header: "Event types", width: 220, cell: (w) => <span className="font-mono text-xs">{w.eventTypes.join(", ")}</span> },
    {
      id: "active", header: "Status", width: 100,
      cell: (w) => <Badge variant={w.active ? "default" : "warning"}>{w.active ? "active" : "paused"}</Badge>,
    },
    {
      id: "circuit", header: "Circuit", width: 100,
      cell: (w) => <Badge variant={w.circuitState === "open" ? "destructive" : "default"}>{w.circuitState ?? "—"}</Badge>,
    },
    { id: "created", header: "Created", width: 160, cell: (w) => formatLocal(w.createdAt) },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm">
          <WebhookIcon className="size-4" aria-hidden /> {t("notifAdmin.webhooks.title")}
        </CardTitle>
        <Can gate={FEATURE_GATES.createNotificationWebhook}>
          <Button size="sm" onClick={() => setCreating((v) => !v)}>
            {creating ? t("action.cancel") : t("notifAdmin.webhooks.new")}
          </Button>
        </Can>
      </CardHeader>
      <CardContent className="space-y-3">
        {creating && (
          <NewWebhookForm
            pending={create.isPending}
            error={create.error}
            onCreate={(input) =>
              create.mutate(input, {
                onSuccess: (w) => {
                  setCreating(false);
                  const v1 = w.secrets.find((s) => s.secret);
                  if (v1?.secret) setCreatedSecret({ url: w.url, secret: v1.secret });
                },
              })
            }
          />
        )}
        {createdSecret && (
          <SecretBanner
            label={`${t("notifAdmin.webhooks.secretOnce")} (${createdSecret.url})`}
            secret={createdSecret.secret}
            onDismiss={() => setCreatedSecret(null)}
          />
        )}
        <div className="grid gap-3 xl:grid-cols-[1fr_420px]">
          <AsyncBoundary
            isLoading={query.isLoading} isError={query.isError} error={query.error}
            isEmpty={rows.length === 0} emptyTitle={t("notifAdmin.webhooks.empty")} onRetry={() => query.refetch()}
          >
            <DataTable
              ariaLabel={t("notifAdmin.webhooks.title")}
              rows={rows}
              columns={columns}
              rowId={(w) => w.id}
              onRowActivate={(w) => setSelectedId(w.id)}
            />
          </AsyncBoundary>
          <WebhookDetail webhook={selected} onClose={() => setSelectedId(null)} />
        </div>
      </CardContent>
    </Card>
  );
}

function SecretBanner({ label, secret, onDismiss }: { label: string; secret: string; onDismiss: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-warning/50 bg-warning/10 p-3 text-xs">
      <span className="font-medium">{label}</span>
      <code data-testid="webhook-secret" className="rounded bg-background px-2 py-1 font-mono">{secret}</code>
      <Button variant="ghost" size="sm" onClick={() => void navigator.clipboard?.writeText(secret)}>Copy</Button>
      <Button variant="ghost" size="icon" aria-label="Dismiss secret" onClick={onDismiss}><X className="size-4" /></Button>
    </div>
  );
}

function NewWebhookForm({
  onCreate, pending, error,
}: {
  onCreate: (input: { url: string; eventTypes: string[] }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [url, setUrl] = useState("");
  const [eventTypes, setEventTypes] = useState("");

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const ev = eventTypes.split(",").map((s) => s.trim()).filter(Boolean);
        if (!url.trim() || ev.length === 0) return;
        onCreate({ url: url.trim(), eventTypes: ev });
      }}
    >
      <label className="flex min-w-72 flex-1 flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Endpoint URL (https)</span>
        <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.example.com/windrose" aria-label="Webhook URL" className="h-8 text-xs" />
      </label>
      <label className="flex min-w-56 flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Event types (comma-separated)</span>
        <Input value={eventTypes} onChange={(e) => setEventTypes(e.target.value)} aria-label="Webhook event types" className="h-8 text-xs" />
      </label>
      <Button type="submit" size="sm" disabled={pending}>{pending ? "Verifying…" : t("notifAdmin.webhooks.new")}</Button>
      <p className="w-full text-xs text-muted-foreground">{t("notifAdmin.webhooks.verifyHint")}</p>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}

function WebhookDetail({ webhook, onClose }: { webhook: WebhookEndpoint | null; onClose: () => void }) {
  const rotate = useRotateNotificationWebhookSecret();
  const update = useUpdateNotificationWebhook();
  const del = useDeleteNotificationWebhook();
  const redeliver = useRedeliverNotificationWebhookDelivery();
  const deliveries = useNotificationWebhookDeliveries(webhook?.id ?? null);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [rotatedSecret, setRotatedSecret] = useState<string | null>(null);
  const rows = useMemo(() => deliveries.data?.pages.flatMap((p) => p.nodes) ?? [], [deliveries.data]);

  if (!webhook) {
    return (
      <Card className="h-fit">
        <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
          <WebhookIcon className="size-6" aria-hidden />
          <p>Select a webhook to see its delivery history, rotate its secret, or delete it.</p>
        </CardContent>
      </Card>
    );
  }

  const deliveryColumns: Column<WebhookDelivery>[] = [
    {
      id: "status", header: "Status", width: 100,
      cell: (d) => <Badge variant={d.status === "failed" ? "destructive" : "default"}>{d.status}</Badge>,
    },
    { id: "attempts", header: "Attempts", width: 80, cell: (d) => d.attempts },
    { id: "error", header: "Last error", cell: (d) => <span className="text-xs">{d.lastError ?? "—"}</span> },
    { id: "at", header: "Updated", width: 150, cell: (d) => formatLocal(d.updatedAt) },
    {
      id: "redeliver", header: "", width: 110,
      cell: (d) => (
        <Can gate={FEATURE_GATES.redeliverNotificationWebhook}>
          <Button
            variant="ghost" size="sm" disabled={redeliver.isPending}
            onClick={(e) => { e.stopPropagation(); redeliver.mutate({ webhookId: webhook.id, deliveryId: d.id }); }}
          >
            <RefreshCcw className="size-3.5" /> {t("notifAdmin.webhooks.redeliver")}
          </Button>
        </Can>
      ),
    },
  ];

  return (
    <Card className="h-fit">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="max-w-[280px] truncate font-mono text-xs">{webhook.url}</CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">
          verified {formatLocal(webhook.verifiedAt)} · circuit {webhook.circuitState ?? "—"} ·{" "}
          {webhook.secrets.length} secret version{webhook.secrets.length === 1 ? "" : "s"}
        </p>
        {rotatedSecret && (
          <SecretBanner
            label={t("notifAdmin.webhooks.secretOnce")}
            secret={rotatedSecret}
            onDismiss={() => setRotatedSecret(null)}
          />
        )}
        <div className="flex flex-wrap gap-2">
          <Can gate={FEATURE_GATES.updateNotificationWebhook}>
            <Button size="sm" variant="outline" onClick={() => setEditing((v) => !v)}>
              {editing ? t("action.cancel") : t("notifAdmin.webhooks.edit")}
            </Button>
          </Can>
          <Can gate={FEATURE_GATES.updateNotificationWebhook}>
            <Button size="sm" variant="outline" disabled={rotate.isPending} onClick={() => setConfirmRotate(true)}>
              {t("notifAdmin.webhooks.rotate")}
            </Button>
          </Can>
          <Can gate={FEATURE_GATES.deleteNotificationWebhook}>
            <Button size="sm" variant="destructive" disabled={del.isPending} onClick={() => setConfirmDelete(true)}>
              {t("action.delete")}
            </Button>
          </Can>
        </div>

        {editing && (
          <EditWebhookForm
            key={webhook.id}
            webhook={webhook}
            pending={update.isPending}
            error={update.error}
            onSave={(input) =>
              update.mutate(
                { id: webhook.id, input },
                { onSuccess: () => setEditing(false) },
              )
            }
          />
        )}

        <p className="text-xs font-medium text-muted-foreground">{t("notifAdmin.webhooks.deliveries")}</p>
        <AsyncBoundary
          isLoading={deliveries.isLoading} isError={deliveries.isError} error={deliveries.error}
          isEmpty={rows.length === 0} emptyTitle="No deliveries recorded for this endpoint yet."
          onRetry={() => deliveries.refetch()}
        >
          <DataTable ariaLabel={t("notifAdmin.webhooks.deliveries")} rows={rows} columns={deliveryColumns} rowId={(d) => d.id} />
        </AsyncBoundary>
      </CardContent>

      <ConfirmDialog
        open={confirmRotate}
        onOpenChange={setConfirmRotate}
        title={t("notifAdmin.webhooks.confirmRotate.title")}
        description={t("notifAdmin.webhooks.confirmRotate.description")}
        confirmLabel={t("notifAdmin.webhooks.rotate")}
        onConfirm={() => {
          setConfirmRotate(false);
          rotate.mutate(webhook.id, {
            onSuccess: (w) => {
              const latest = [...w.secrets].sort((a, b) => b.version - a.version)[0];
              if (latest?.secret) setRotatedSecret(latest.secret);
            },
          });
        }}
      />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t("notifAdmin.webhooks.confirmDelete.title")}
        description={t("notifAdmin.webhooks.confirmDelete.description")}
        confirmLabel={t("action.delete")}
        confirmPhrase={webhook.url}
        destructive
        onConfirm={() => { setConfirmDelete(false); del.mutate(webhook.id, { onSuccess: onClose }); }}
      />
    </Card>
  );
}

function EditWebhookForm({
  webhook, onSave, pending, error,
}: {
  webhook: WebhookEndpoint;
  onSave: (input: { url?: string; eventTypes?: string[]; active?: boolean }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [url, setUrl] = useState(webhook.url);
  const [eventTypes, setEventTypes] = useState(webhook.eventTypes.join(", "));
  const [active, setActive] = useState(webhook.active);

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const ev = eventTypes.split(",").map((s) => s.trim()).filter(Boolean);
        if (!url.trim() || ev.length === 0) return;
        onSave({ url: url.trim(), eventTypes: ev, active });
      }}
    >
      <label className="flex min-w-72 flex-1 flex-col gap-1 text-xs">
        <span className="text-muted-foreground">{t("notifAdmin.webhooks.url")}</span>
        <Input value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Edit webhook URL" className="h-8 text-xs" />
      </label>
      <label className="flex min-w-56 flex-col gap-1 text-xs">
        <span className="text-muted-foreground">{t("notifAdmin.webhooks.events")}</span>
        <Input value={eventTypes} onChange={(e) => setEventTypes(e.target.value)} aria-label="Edit webhook event types" className="h-8 text-xs" />
      </label>
      <label className="flex items-center gap-1.5 pb-1.5 text-xs">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} aria-label="Webhook active" />
        <span className="text-muted-foreground">{t("notifAdmin.webhooks.activeLabel")}</span>
      </label>
      <Button type="submit" size="sm" disabled={pending}>{t("action.save")}</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------
function TemplatesCard() {
  const [key, setKey] = useState("");
  const [activeKey, setActiveKey] = useState("");
  const query = useNotificationTemplates(activeKey);
  const create = useCreateNotificationTemplate();
  const publish = usePublishNotificationTemplate();
  const preview = usePreviewNotificationTemplate();
  const [creating, setCreating] = useState(false);

  const columns: Column<NotificationTemplate>[] = [
    { id: "version", header: "v", width: 50, cell: (x) => x.version },
    { id: "channel", header: "Channel", width: 90, cell: (x) => x.channel },
    { id: "locale", header: "Locale", width: 70, cell: (x) => x.locale },
    {
      id: "status", header: "Status", width: 110,
      cell: (x) => <Badge variant={x.status === "published" ? "default" : "warning"}>{x.status}</Badge>,
    },
    { id: "subject", header: "Subject", cell: (x) => <span className="font-mono text-xs">{x.subjectTpl ?? "—"}</span> },
    {
      id: "actions", header: "", width: 100,
      cell: (x) =>
        x.status !== "published" ? (
          <Can gate={FEATURE_GATES.publishNotificationTemplate}>
            <Button
              variant="ghost" size="sm" disabled={publish.isPending}
              onClick={(e) => { e.stopPropagation(); publish.mutate({ key: x.key, templateId: x.id }); }}
            >
              {t("notifAdmin.templates.publish")}
            </Button>
          </Can>
        ) : null,
    },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm">
          <FileText className="size-4" aria-hidden /> {t("notifAdmin.templates.title")}
        </CardTitle>
        <Can gate={FEATURE_GATES.createNotificationTemplate}>
          <Button size="sm" onClick={() => setCreating((v) => !v)}>
            {creating ? t("action.cancel") : "New draft"}
          </Button>
        </Can>
      </CardHeader>
      <CardContent className="space-y-3">
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => { e.preventDefault(); setActiveKey(key.trim()); }}
        >
          <label className="flex flex-1 flex-col gap-1 text-xs">
            <span className="text-muted-foreground">{t("notifAdmin.templates.hint")}</span>
            <Input value={key} onChange={(e) => setKey(e.target.value)} placeholder="case.assigned.v1" aria-label="Template key" className="h-8 text-xs" />
          </label>
          <Button type="submit" size="sm" variant="outline">Load</Button>
        </form>

        {creating && (
          <NewTemplateForm
            defaultKey={activeKey || key}
            pending={create.isPending}
            error={create.error}
            onCreate={(input) => create.mutate(input, { onSuccess: (d) => { setCreating(false); setKey(d.key); setActiveKey(d.key); } })}
          />
        )}

        {activeKey && (
          <AsyncBoundary
            isLoading={query.isLoading} isError={query.isError} error={query.error}
            isEmpty={(query.data ?? []).length === 0} emptyTitle={`No template versions for "${activeKey}".`}
            onRetry={() => query.refetch()}
          >
            <DataTable ariaLabel={t("notifAdmin.templates.title")} rows={query.data ?? []} columns={columns} rowId={(x) => x.id} />
          </AsyncBoundary>
        )}

        {activeKey && (
          <Can gate={FEATURE_GATES.viewNotificationTemplates}>
            <div className="space-y-2">
              <Button
                size="sm" variant="outline" disabled={preview.isPending}
                onClick={() => preview.mutate({ key: activeKey, channel: "email" })}
              >
                {t("notifAdmin.templates.preview")}
              </Button>
              {preview.data && (
                <div className="rounded-md border p-3 text-xs">
                  <p className="font-medium">{preview.data.subject}</p>
                  <pre className="mt-1 whitespace-pre-wrap text-muted-foreground">{preview.data.text}</pre>
                </div>
              )}
              {preview.error && <p className="text-xs text-destructive">{preview.error.message}</p>}
            </div>
          </Can>
        )}
      </CardContent>
    </Card>
  );
}

function NewTemplateForm({
  defaultKey, onCreate, pending, error,
}: {
  defaultKey: string;
  onCreate: (input: { key: string; channel: string; locale?: string; subjectTpl?: string; bodyTextTpl?: string; bodyHtmlTpl?: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [key, setKey] = useState(defaultKey);
  const [channel, setChannel] = useState("email");
  const [subject, setSubject] = useState("");
  const [bodyText, setBodyText] = useState("");

  return (
    <form
      className="space-y-2 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!key.trim() || !channel.trim()) return;
        onCreate({ key: key.trim(), channel, subjectTpl: subject, bodyTextTpl: bodyText, bodyHtmlTpl: bodyText });
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-1 flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Key (event type)</span>
          <Input value={key} onChange={(e) => setKey(e.target.value)} aria-label="New template key" className="h-8 text-xs" />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Channel</span>
          <select value={channel} onChange={(e) => setChannel(e.target.value)} aria-label="New template channel" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
            <option value="email">email</option>
            <option value="inapp">inapp</option>
          </select>
        </label>
      </div>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Subject template</span>
        <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Case {{.CaseNumber}} assigned" aria-label="Subject template" className="h-8 font-mono text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Body template (variables are whitelisted per event type)</span>
        <Textarea value={bodyText} onChange={(e) => setBodyText(e.target.value)} aria-label="Body template" className="min-h-[70px] font-mono text-xs" />
      </label>
      <Button type="submit" size="sm" disabled={pending}>Create draft</Button>
      {error && <p className="text-xs text-destructive">{error.message}</p>}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Ops: delivery stats + suppressions
// ---------------------------------------------------------------------------
function OpsCard() {
  const stats = useNotificationDeliveryStats("24h");
  const suppressions = useEmailSuppressions();
  const clear = useClearEmailSuppression();
  const [confirmClear, setConfirmClear] = useState<EmailSuppression | null>(null);

  const columns: Column<EmailSuppression>[] = [
    { id: "hash", header: "Email hash", cell: (s) => <span className="font-mono text-xs">{s.emailHash}</span> },
    { id: "reason", header: "Reason", width: 110, cell: (s) => <Badge variant="warning">{s.reason}</Badge> },
    { id: "created", header: "Since", width: 150, cell: (s) => formatLocal(s.createdAt) },
    {
      id: "clear", header: "", width: 90,
      cell: (s) => (
        <Can gate={FEATURE_GATES.clearEmailSuppression}>
          <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setConfirmClear(s); }}>
            {t("notifAdmin.ops.clearSuppression")}
          </Button>
        </Can>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Activity className="size-4" aria-hidden /> {t("notifAdmin.ops.title")}
        </CardTitle>
        <CardDescription>Delivery counts by channel over the last 24h, plus suppressed email recipients.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <AsyncBoundary
          isLoading={stats.isLoading} isError={stats.isError} error={stats.error}
          isEmpty={!stats.data} emptyTitle="No delivery stats." onRetry={() => stats.refetch()}
        >
          <pre data-testid="delivery-stats" className="overflow-auto rounded-md border bg-muted/40 p-3 text-xs">
            {JSON.stringify(stats.data?.byChannel ?? {}, null, 2)}
          </pre>
        </AsyncBoundary>

        <p className="text-xs font-medium text-muted-foreground">{t("notifAdmin.ops.suppressions")}</p>
        <AsyncBoundary
          isLoading={suppressions.isLoading} isError={suppressions.isError} error={suppressions.error}
          isEmpty={(suppressions.data ?? []).length === 0} emptyTitle="No suppressed recipients."
          onRetry={() => suppressions.refetch()}
        >
          <DataTable ariaLabel={t("notifAdmin.ops.suppressions")} rows={suppressions.data ?? []} columns={columns} rowId={(s) => s.id} />
        </AsyncBoundary>
      </CardContent>

      <ConfirmDialog
        open={confirmClear !== null}
        onOpenChange={(o) => !o && setConfirmClear(null)}
        title={t("notifAdmin.ops.confirmClear.title")}
        description={t("notifAdmin.ops.confirmClear.description")}
        confirmLabel={t("notifAdmin.ops.clearSuppression")}
        onConfirm={() => { if (confirmClear) clear.mutate(confirmClear.emailHash); setConfirmClear(null); }}
      />
    </Card>
  );
}
