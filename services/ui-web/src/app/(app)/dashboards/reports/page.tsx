"use client";
import { useMemo, useState } from "react";
import { LineChart, Plus, Loader2 } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter, Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { CreateReportSubscriptionDialog } from "@/components/charts/CreateReportSubscriptionDialog";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useReportSubscriptions, useDashboards, usePauseReportSubscription,
  useDeleteReportSubscription, useTriggerReportSubscription,
} from "@/lib/graphql/hooks";
import type { ReportSubscription } from "@/lib/graphql/types";
import { useSession } from "@/lib/session/SessionContext";
import { formatLocal } from "@/lib/utils";
import { t, type MessageKey } from "@/lib/i18n/messages";

function cadenceLabel(sub: ReportSubscription): string {
  const hour = String(sub.sendHour).padStart(2, "0") + ":00 UTC";
  if (sub.cadence === "weekly") {
    const day = sub.sendWeekday != null ? t(`reports.weekday.${sub.sendWeekday}` as MessageKey) : "";
    return `${t("reports.cadenceWeekly")} · ${day} ${hour}`;
  }
  return `${t("reports.cadenceDaily")} · ${hour}`;
}

/**
 * Team reports (NOTIF-FR-060): list + manage scheduled dashboard-email
 * subscriptions. Mirrors dashboards/page.tsx's card-list shape (not DataTable
 * — a subscription's row actions and two-line status don't fit that grid, and
 * a handful of subscriptions per tenant never needs virtualization).
 */
export default function DashboardReportsPage() {
  const { workspaceId } = useSession();
  const query = useReportSubscriptions();
  const items = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  // Best-effort dashboard-title lookup for the caller's own workspace (a
  // subscription's dashboard usually lives there); falls back to the raw id.
  const dashboardsQuery = useDashboards(workspaceId);
  const dashboardTitles = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of dashboardsQuery.data?.pages.flatMap((p) => p.nodes) ?? []) m.set(d.id, d.title);
    return m;
  }, [dashboardsQuery.data]);

  const [creating, setCreating] = useState(false);
  const [editSub, setEditSub] = useState<ReportSubscription | null>(null);
  const [toDelete, setToDelete] = useState<ReportSubscription | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [triggeringId, setTriggeringId] = useState<string | null>(null);

  const pauseMutation = usePauseReportSubscription();
  const deleteMutation = useDeleteReportSubscription();
  const triggerMutation = useTriggerReportSubscription();

  const onCreated = () => {
    setCreating(false);
    setBanner(t("reports.created"));
  };

  const onTogglePause = (sub: ReportSubscription) => {
    pauseMutation.mutate({ id: sub.id, paused: sub.enabled });
  };

  const onSendNow = (sub: ReportSubscription) => {
    setTriggeringId(sub.id);
    setBanner(null);
    triggerMutation.mutate(sub.id, {
      onSuccess: () => setBanner(t("reports.sendNowSuccess", { recipients: sub.recipients.join(", ") })),
      onError: (e) => setBanner(e.message),
      onSettled: () => setTriggeringId(null),
    });
  };

  return (
    <div>
      <PageHeader
        title={t("reports.title")}
        description={t("reports.subtitle")}
        actions={
          <Can gate={FEATURE_GATES.createReportSubscription}>
            <Button onClick={() => setCreating(true)}>
              <Plus /> {t("reports.create")}
            </Button>
          </Can>
        }
      />

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="report-banner">
          {banner}
        </div>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={items.length === 0}
        emptyTitle={t("reports.empty")}
        emptyCta={
          <Can gate={FEATURE_GATES.createReportSubscription}>
            <Button className="mt-2" onClick={() => setCreating(true)}>
              <Plus /> {t("reports.create")}
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <div className="space-y-3">
          {items.map((s) => (
            <Card key={s.id}>
              <CardHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <LineChart className="size-4 text-muted-foreground" aria-hidden />
                  <CardTitle className="text-base">{s.name}</CardTitle>
                  <Badge variant={s.enabled ? "default" : "secondary"}>
                    {s.enabled ? t("reports.enabled") : t("reports.paused")}
                  </Badge>
                </div>
                <CardDescription>{dashboardTitles.get(s.dashboardId) ?? s.dashboardId}</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-1 text-sm sm:grid-cols-3">
                <div>
                  <div className="text-xs text-muted-foreground">{t("reports.cadence")}</div>
                  <div>{cadenceLabel(s)}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">{t("reports.recipients")}</div>
                  <div className="truncate">{s.recipients.join(", ")}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">{t("reports.lastSent")}</div>
                  {s.lastStatus === "failed" ? (
                    <div className="text-destructive" title={s.lastError ?? ""}>
                      {t("reports.lastStatusFailed", { error: s.lastError ?? "" })}
                    </div>
                  ) : s.lastSentAt ? (
                    <div>{formatLocal(s.lastSentAt)}</div>
                  ) : (
                    <div className="text-muted-foreground">{t("reports.neverSent")}</div>
                  )}
                </div>
              </CardContent>
              <CardFooter className="flex justify-end gap-1">
                <Button variant="outline" size="sm" onClick={() => onSendNow(s)} disabled={triggeringId === s.id}>
                  {triggeringId === s.id ? <Loader2 className="animate-spin" /> : t("reports.sendNow")}
                </Button>
                <Can gate={FEATURE_GATES.updateReportSubscription}>
                  <Button variant="ghost" size="sm" onClick={() => setEditSub(s)}>
                    {t("reports.edit")}
                  </Button>
                </Can>
                <Can gate={FEATURE_GATES.updateReportSubscription}>
                  <Button variant="ghost" size="sm" onClick={() => onTogglePause(s)} disabled={pauseMutation.isPending}>
                    {s.enabled ? t("reports.pause") : t("reports.resume")}
                  </Button>
                </Can>
                <Can gate={FEATURE_GATES.deleteReportSubscription}>
                  <Button variant="ghost" size="sm" onClick={() => setToDelete(s)}>
                    {t("reports.delete")}
                  </Button>
                </Can>
              </CardFooter>
            </Card>
          ))}
          {query.hasNextPage && (
            <div className="flex justify-center">
              <Button variant="outline" size="sm" onClick={() => query.fetchNextPage()} disabled={query.isFetchingNextPage}>
                {query.isFetchingNextPage ? <Loader2 className="animate-spin" /> : "Load more"}
              </Button>
            </div>
          )}
        </div>
      </AsyncBoundary>

      <CreateReportSubscriptionDialog open={creating} onOpenChange={setCreating} onCreated={onCreated} />

      <CreateReportSubscriptionDialog
        open={!!editSub}
        onOpenChange={(o) => !o && setEditSub(null)}
        subscription={editSub}
        onUpdated={() => {
          setEditSub(null);
          setBanner(t("reports.updated"));
        }}
      />

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t("reports.delete")}
        description={toDelete ? t("reports.deleteConfirm", { name: toDelete.name }) : ""}
        confirmLabel={t("reports.delete")}
        destructive
        onConfirm={() => {
          if (toDelete)
            deleteMutation.mutate(toDelete.id, {
              onSuccess: () => setBanner(t("reports.deleted")),
              onSettled: () => setToDelete(null),
            });
        }}
      />
    </div>
  );
}
