"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BellOff, CheckCheck } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardDescription, CardContent, Input, Textarea, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useNotifications, useMarkNotificationRead, useMarkAllNotificationsRead,
  useNotificationPreferences, useUpdateNotificationPreferences,
} from "@/lib/graphql/hooks";
import type { Notification, NotificationPreferences } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";
import { formatLocal } from "@/lib/utils";

/**
 * In-app notification inbox + per-user delivery preferences (Tier 2b,
 * notification-service NOTIF-FR-020/012 via the bff). Distinct from the agent
 * PROPOSALS inbox at /inbox — this is the event-notification stream (case
 * assigned, ingestion finished, budget threshold, …).
 */
export default function NotificationsPage() {
  return (
    <div>
      <PageHeader title={t("notifications.title")} description={t("notifications.subtitle")} />
      <div className="space-y-4">
        <InboxCard />
        <Can gate={FEATURE_GATES.viewNotificationPreferences}>
          <PreferencesCard />
        </Can>
      </div>
    </div>
  );
}

function severityVariant(s?: string | null): "default" | "warning" | "destructive" {
  if (s === "critical") return "destructive";
  if (s === "action") return "warning";
  return "default";
}

function InboxCard() {
  const router = useRouter();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const filters = useMemo(() => (unreadOnly ? { unread: true } : {}), [unreadOnly]);
  const query = useNotifications(filters);
  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const columns: Column<Notification>[] = [
    {
      id: "title",
      header: "Notification",
      cell: (n) => (
        <span className={n.readAt ? "text-muted-foreground" : "font-medium"}>{n.title}</span>
      ),
    },
    {
      id: "severity", header: "Severity", width: 110,
      cell: (n) => <Badge variant={severityVariant(n.severityClass)}>{n.severityClass ?? "info"}</Badge>,
    },
    { id: "event", header: "Event", width: 200, cell: (n) => <span className="font-mono text-xs">{n.eventType}</span> },
    { id: "created", header: "Received", width: 170, cell: (n) => formatLocal(n.createdAt) },
    {
      id: "read", header: "", width: 120,
      cell: (n) => (
        <Button
          variant="ghost"
          size="sm"
          disabled={markRead.isPending}
          onClick={(e) => {
            e.stopPropagation();
            markRead.mutate({ id: n.id, read: !n.readAt });
          }}
        >
          {n.readAt ? t("notifications.markUnread") : t("notifications.markRead")}
        </Button>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">{t("notifications.title")}</CardTitle>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(e) => setUnreadOnly(e.target.checked)}
              aria-label={t("notifications.unreadOnly")}
            />
            <span className="text-muted-foreground">{t("notifications.unreadOnly")}</span>
          </label>
          <Button size="sm" variant="outline" disabled={markAll.isPending} onClick={() => markAll.mutate()}>
            <CheckCheck className="size-4" /> {t("notifications.markAllRead")}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={rows.length === 0}
          emptyTitle={t("notifications.empty")}
          onRetry={() => query.refetch()}
        >
          <DataTable
            ariaLabel={t("notifications.title")}
            rows={rows}
            columns={columns}
            rowId={(n) => n.id}
            hasMore={query.hasNextPage}
            isFetchingMore={query.isFetchingNextPage}
            onLoadMore={() => query.fetchNextPage()}
            onRowActivate={(n) => {
              // Opening a notification marks it read and follows its deep link.
              if (!n.readAt) markRead.mutate({ id: n.id, read: true });
              if (n.deepLink) router.push(n.deepLink);
            }}
            emptyState={
              <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
                <BellOff className="size-8" />
                <p>{t("notifications.empty")}</p>
              </div>
            }
          />
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}

/** Per-user preferences (GET/PUT /preferences). Channel overrides and digest
 * config are open maps downstream (event_type -> channels / event_class ->
 * window), edited here as JSON; quiet hours and mutes get dedicated fields. */
function PreferencesCard() {
  const query = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();

  if (query.isLoading) return null;
  if (query.isError || !query.data) return null;
  return <PreferencesForm prefs={query.data} pending={update.isPending} error={update.error} saved={update.isSuccess} onSave={(input) => update.mutate(input)} />;
}

function PreferencesForm({
  prefs,
  pending,
  error,
  saved,
  onSave,
}: {
  prefs: NotificationPreferences;
  pending: boolean;
  error: Error | null;
  saved: boolean;
  onSave: (input: {
    channelOverrides?: Record<string, string[]>;
    mutes?: unknown;
    quietHours?: unknown;
    digestConfig?: Record<string, string>;
  }) => void;
}) {
  const [channelOverrides, setChannelOverrides] = useState(JSON.stringify(prefs.channelOverrides ?? {}, null, 2));
  const [digestConfig, setDigestConfig] = useState(JSON.stringify(prefs.digestConfig ?? {}, null, 2));
  const [mutedEvents, setMutedEvents] = useState((prefs.mutes?.event_types ?? []).join(", "));
  const [qhTz, setQhTz] = useState(prefs.quietHours?.tz ?? "");
  const [qhStart, setQhStart] = useState(prefs.quietHours?.start ?? "");
  const [qhEnd, setQhEnd] = useState(prefs.quietHours?.end ?? "");
  const [parseError, setParseError] = useState<string | null>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    let overrides: Record<string, string[]>;
    let digest: Record<string, string>;
    try {
      overrides = JSON.parse(channelOverrides || "{}");
      digest = JSON.parse(digestConfig || "{}");
    } catch {
      setParseError("Channel overrides / digest config must be valid JSON objects.");
      return;
    }
    setParseError(null);
    const eventTypes = mutedEvents.split(",").map((s) => s.trim()).filter(Boolean);
    onSave({
      channelOverrides: overrides,
      digestConfig: digest,
      mutes: eventTypes.length > 0 ? { event_types: eventTypes } : {},
      quietHours: qhTz && qhStart && qhEnd ? { tz: qhTz, start: qhStart, end: qhEnd } : null,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t("notifications.preferences.title")}</CardTitle>
        <CardDescription>{t("notifications.preferences.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-3">
          <div className="grid gap-3 lg:grid-cols-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Channel overrides (event type → channels, JSON)</span>
              <Textarea
                value={channelOverrides}
                onChange={(e) => setChannelOverrides(e.target.value)}
                aria-label="Channel overrides"
                className="min-h-[90px] font-mono text-xs"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Digest config (event class → window, JSON)</span>
              <Textarea
                value={digestConfig}
                onChange={(e) => setDigestConfig(e.target.value)}
                aria-label="Digest config"
                className="min-h-[90px] font-mono text-xs"
              />
            </label>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-64 flex-1 flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Muted event types (comma-separated)</span>
              <Input value={mutedEvents} onChange={(e) => setMutedEvents(e.target.value)} aria-label="Muted event types" className="h-8 text-xs" />
            </label>
            <Label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Quiet hours TZ</span>
              <Input value={qhTz} onChange={(e) => setQhTz(e.target.value)} placeholder="America/New_York" aria-label="Quiet hours timezone" className="h-8 w-44 text-xs" />
            </Label>
            <Label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Start</span>
              <Input value={qhStart} onChange={(e) => setQhStart(e.target.value)} placeholder="22:00" aria-label="Quiet hours start" className="h-8 w-20 text-xs" />
            </Label>
            <Label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">End</span>
              <Input value={qhEnd} onChange={(e) => setQhEnd(e.target.value)} placeholder="07:00" aria-label="Quiet hours end" className="h-8 w-20 text-xs" />
            </Label>
          </div>
          <div className="flex items-center gap-3">
            <Can gate={FEATURE_GATES.updateNotificationPreferences}>
              <Button type="submit" size="sm" disabled={pending}>{t("notifications.preferences.save")}</Button>
            </Can>
            {saved && !pending && <span className="text-xs text-muted-foreground">{t("notifications.preferences.saved")}</span>}
            {(parseError || error) && <span className="text-xs text-destructive">{parseError ?? error?.message}</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
