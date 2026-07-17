"use client";
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Input, Label, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCreateReportSubscription, useUpdateReportSubscription, useDashboards } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { useSession } from "@/lib/session/SessionContext";
import type { ReportSubscription, UpdateReportSubscriptionInput } from "@/lib/graphql/types";
import { t, type MessageKey } from "@/lib/i18n/messages";

const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/**
 * Subscribe a dashboard to a scheduled email digest (NOTIF-FR-060). Mirrors
 * CreateDashboardDialog's shape: a single form, an inline banner for
 * validation, and the mutation's own GraphQLRequestError surfaced verbatim
 * (the backend's real 422/403 is what the user sees — nothing synthesized).
 *
 * Doubles as the EDIT dialog: pass `subscription` to hydrate the form from an
 * existing subscription and save via updateReportSubscription (the dashboard
 * itself isn't editable — updateReportSubscription has no dashboardId field).
 */
export function CreateReportSubscriptionDialog({
  open,
  onOpenChange,
  defaultDashboardId,
  subscription,
  onCreated,
  onUpdated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  /** Pre-select a dashboard (e.g. opened from that dashboard's own page). */
  defaultDashboardId?: string;
  /** When set, the dialog opens in EDIT mode, hydrated from this subscription. */
  subscription?: ReportSubscription | null;
  onCreated?: (id: string) => void;
  onUpdated?: () => void;
}) {
  const { workspaceId } = useSession();
  const dashboardsQuery = useDashboards(workspaceId);
  const dashboards = useMemo(() => dashboardsQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [dashboardsQuery.data]);

  const [dashboardId, setDashboardId] = useState(defaultDashboardId ?? "");
  const [name, setName] = useState("");
  const [recipients, setRecipients] = useState("");
  const [cadence, setCadence] = useState<"daily" | "weekly">("weekly");
  const [sendHour, setSendHour] = useState(8);
  const [sendWeekday, setSendWeekday] = useState(1);
  const [format, setFormat] = useState<"html" | "text">("html");
  const [timezone, setTimezone] = useState("UTC");
  const [banner, setBanner] = useState<string | null>(null);
  const createMutation = useCreateReportSubscription();
  const updateMutation = useUpdateReportSubscription();
  const activeMutation = subscription ? updateMutation : createMutation;

  useEffect(() => {
    if (!open) return;
    setBanner(null);
    if (subscription) {
      setDashboardId(subscription.dashboardId);
      setName(subscription.name);
      setRecipients(subscription.recipients.join(", "));
      setCadence(subscription.cadence === "daily" ? "daily" : "weekly");
      setSendHour(subscription.sendHour);
      setSendWeekday(subscription.sendWeekday ?? 1);
      setFormat(subscription.format === "text" ? "text" : "html");
      setTimezone(subscription.timezone);
      return;
    }
    setDashboardId(defaultDashboardId ?? "");
    setName("");
    setRecipients("");
    setCadence("weekly");
    setSendHour(8);
    setSendWeekday(1);
    setFormat("html");
    setTimezone("UTC");
  }, [open, defaultDashboardId, subscription]);

  const submit = () => {
    setBanner(null);
    if (!dashboardId) {
      setBanner(t("reports.dashboardRequired"));
      return;
    }
    if (!name.trim()) {
      setBanner(t("reports.nameRequired"));
      return;
    }
    const emails = recipients
      .split(/[,\n]/)
      .map((e) => e.trim())
      .filter(Boolean);
    if (emails.length === 0) {
      setBanner(t("reports.recipientsRequired"));
      return;
    }
    const bad = emails.find((e) => !EMAIL_RE.test(e));
    if (bad) {
      setBanner(t("reports.invalidRecipient", { email: bad }));
      return;
    }
    if (subscription) {
      const input: UpdateReportSubscriptionInput = {
        name: name.trim(),
        recipients: emails,
        cadence,
        sendHour,
        sendWeekday: cadence === "weekly" ? sendWeekday : undefined,
        timezone,
        format,
      };
      updateMutation.mutate({ id: subscription.id, input }, { onSuccess: () => onUpdated?.() });
      return;
    }
    createMutation.mutate(
      {
        dashboardId,
        name: name.trim(),
        recipients: emails,
        cadence,
        sendHour,
        sendWeekday: cadence === "weekly" ? sendWeekday : undefined,
        format,
      },
      { onSuccess: (r) => onCreated?.(r.createReportSubscription.id) },
    );
  };

  const error = activeMutation.error instanceof GraphQLRequestError ? activeMutation.error : null;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">
            {subscription ? t("reports.editTitle") : t("reports.createTitle")}
          </Dialog.Title>
          <form
            className="mt-4 max-h-[70vh] space-y-3 overflow-y-auto"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="report-dashboard">{t("reports.dashboard")}</Label>
              <select
                id="report-dashboard"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={dashboardId}
                onChange={(e) => setDashboardId(e.target.value)}
                disabled={!!defaultDashboardId || !!subscription}
              >
                <option value="">{t("reports.pickDashboard")}</option>
                {dashboards.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="report-name">{t("reports.name")}</Label>
              <Input
                id="report-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("reports.namePlaceholder")}
                autoFocus
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="report-recipients">{t("reports.recipients")}</Label>
              <Textarea
                id="report-recipients"
                value={recipients}
                onChange={(e) => setRecipients(e.target.value)}
                placeholder={t("reports.recipientsPlaceholder")}
                rows={2}
              />
              <p className="text-xs text-muted-foreground">{t("reports.recipientsHint")}</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="report-cadence">{t("reports.cadence")}</Label>
                <select
                  id="report-cadence"
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  value={cadence}
                  onChange={(e) => setCadence(e.target.value as "daily" | "weekly")}
                >
                  <option value="daily">{t("reports.cadenceDaily")}</option>
                  <option value="weekly">{t("reports.cadenceWeekly")}</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="report-hour">{t("reports.sendHour")}</Label>
                <Input
                  id="report-hour"
                  type="number"
                  min={0}
                  max={23}
                  value={sendHour}
                  onChange={(e) => setSendHour(Number(e.target.value))}
                />
              </div>
            </div>

            {cadence === "weekly" && (
              <div className="space-y-1.5">
                <Label htmlFor="report-weekday">{t("reports.sendWeekday")}</Label>
                <select
                  id="report-weekday"
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  value={sendWeekday}
                  onChange={(e) => setSendWeekday(Number(e.target.value))}
                >
                  {WEEKDAYS.map((wd) => (
                    <option key={wd} value={wd}>
                      {t(`reports.weekday.${wd}` as MessageKey)}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="report-format">{t("reports.format")}</Label>
              <select
                id="report-format"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={format}
                onChange={(e) => setFormat(e.target.value as "html" | "text")}
              >
                <option value="html">{t("reports.formatHtml")}</option>
                <option value="text">{t("reports.formatText")}</option>
              </select>
            </div>

            {banner && <p className="text-xs text-muted-foreground">{banner}</p>}
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <Dialog.Close asChild>
                <Button type="button" variant="outline">
                  Cancel
                </Button>
              </Dialog.Close>
              <Button type="submit" disabled={activeMutation.isPending}>
                {activeMutation.isPending
                  ? t(subscription ? "reports.saving" : "reports.creating")
                  : t(subscription ? "reports.save" : "reports.create")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
