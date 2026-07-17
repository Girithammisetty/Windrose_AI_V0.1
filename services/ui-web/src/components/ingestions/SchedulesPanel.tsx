"use client";
import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { CalendarClock, Plus } from "lucide-react";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { Badge, Input, Label, Textarea } from "@/components/ui/primitives";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useConnections,
  useIngestionSchedules,
  useCreateIngestionSchedule,
  useUpdateIngestionSchedule,
  useDeleteIngestionSchedule,
  usePauseIngestionSchedule,
  useResumeIngestionSchedule,
  useRunIngestionScheduleNow,
} from "@/lib/graphql/hooks";
import type { IngestionSchedule } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/**
 * Recurring ingestion schedules (ingestion-service /schedules, ING-FR-060..062):
 * cron or fixed-interval query ingestions with pause/resume/run-now and delete.
 */
export function SchedulesPanel({ onNotice }: { onNotice: (msg: string) => void }) {
  const query = useIngestionSchedules();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const pauseMutation = usePauseIngestionSchedule();
  const resumeMutation = useResumeIngestionSchedule();
  const runNowMutation = useRunIngestionScheduleNow();
  const deleteMutation = useDeleteIngestionSchedule();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<IngestionSchedule | null>(null);
  const [toDelete, setToDelete] = useState<IngestionSchedule | null>(null);

  const columns: Column<IngestionSchedule>[] = [
    {
      id: "timing",
      header: t("schedules.timing"),
      cell: (s) => (
        <span className="font-mono text-xs">
          {s.cron ?? (s.intervalSeconds != null ? `every ${s.intervalSeconds}s` : "—")}
          {s.timezone && s.timezone !== "UTC" ? ` (${s.timezone})` : ""}
        </span>
      ),
    },
    {
      id: "status",
      header: t("schedules.status"),
      width: 110,
      cell: (s) => (
        <Badge variant={s.enabled ? "success" : "secondary"}>{s.enabled ? "enabled" : "paused"}</Badge>
      ),
    },
    {
      id: "connection",
      header: t("schedules.connection"),
      width: 130,
      cell: (s) => <span className="font-mono text-xs">{s.connectionId.slice(0, 8)}</span>,
    },
    { id: "lastFired", header: t("schedules.lastFired"), width: 160, cell: (s) => formatLocal(s.lastFiredAt) },
    { id: "nextFire", header: t("schedules.nextFire"), width: 160, cell: (s) => formatLocal(s.nextFireAt) },
    {
      id: "actions",
      header: t("ingestions.actions"),
      width: 300,
      cell: (s) => (
        <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          <Can gate={FEATURE_GATES.runIngestionScheduleNow}>
            <Button
              variant="outline"
              size="sm"
              disabled={runNowMutation.isPending || !s.enabled}
              onClick={() =>
                runNowMutation.mutate(s.id, {
                  onSuccess: (r) => onNotice(r.skipped ? t("schedules.firedSkipped") : t("schedules.fired")),
                  onError: (e) => onNotice((e as Error).message),
                })
              }
            >
              {t("schedules.runNow")}
            </Button>
          </Can>
          <Can gate={FEATURE_GATES.updateIngestionSchedule}>
            {s.enabled ? (
              <Button
                variant="ghost"
                size="sm"
                disabled={pauseMutation.isPending}
                onClick={() =>
                  pauseMutation.mutate(s.id, { onSuccess: () => onNotice(t("schedules.paused")) })
                }
              >
                {t("schedules.pause")}
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                disabled={resumeMutation.isPending}
                onClick={() =>
                  resumeMutation.mutate(s.id, { onSuccess: () => onNotice(t("schedules.resumed")) })
                }
              >
                {t("schedules.resume")}
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={() => { setEditing(s); setFormOpen(true); }}>
              {t("schedules.edit")}
            </Button>
          </Can>
          <Can gate={FEATURE_GATES.deleteIngestionSchedule}>
            <Button variant="ghost" size="sm" onClick={() => setToDelete(s)}>
              {t("schedules.delete")}
            </Button>
          </Can>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{t("schedules.subtitle")}</p>
        <Can gate={FEATURE_GATES.createIngestionSchedule}>
          <Button size="sm" onClick={() => { setEditing(null); setFormOpen(true); }}>
            <Plus /> {t("schedules.new")}
          </Button>
        </Can>
      </div>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("schedules.empty")}
        emptyCta={
          <Can gate={FEATURE_GATES.createIngestionSchedule}>
            <Button variant="outline" size="sm" className="mt-2" onClick={() => { setEditing(null); setFormOpen(true); }}>
              <Plus /> {t("schedules.new")}
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("schedules.title")}
          rows={rows}
          columns={columns}
          rowId={(s) => s.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <CalendarClock className="size-8" />
              <p>{t("schedules.emptyHint")}</p>
            </div>
          }
        />
      </AsyncBoundary>

      <ScheduleDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editing={editing}
        onSaved={(msg) => {
          setFormOpen(false);
          setEditing(null);
          onNotice(msg);
        }}
      />

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t("schedules.delete")}
        description={t("schedules.deleteConfirm")}
        confirmLabel={t("schedules.delete")}
        destructive
        onConfirm={() => {
          if (toDelete)
            deleteMutation.mutate(toDelete.id, {
              onSuccess: () => onNotice(t("schedules.deleted")),
              onSettled: () => setToDelete(null),
            });
        }}
      />
    </div>
  );
}

/** Create/edit form. Timing is XOR cron/interval — mirrored client-side so the
 * 422 from schedules.py's _validate_timing is pre-empted with a clear message. */
function ScheduleDialog({
  open,
  onOpenChange,
  editing,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  editing: IngestionSchedule | null;
  onSaved: (msg: string) => void;
}) {
  const connections = useConnections();
  const conns = useMemo(() => connections.data?.pages.flatMap((p) => p.nodes) ?? [], [connections.data]);
  const createMutation = useCreateIngestionSchedule();
  const updateMutation = useUpdateIngestionSchedule();
  const pending = createMutation.isPending || updateMutation.isPending;

  const [connectionId, setConnectionId] = useState("");
  const [mode, setMode] = useState<"cron" | "interval">("cron");
  const [cron, setCron] = useState("0 6 * * *");
  const [intervalSeconds, setIntervalSeconds] = useState("3600");
  const [timezone, setTimezone] = useState("UTC");
  const [statement, setStatement] = useState("");
  const [datasetName, setDatasetName] = useState("");
  const [overlapPolicy, setOverlapPolicy] = useState("skip");
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setBanner(null);
    createMutation.reset();
    updateMutation.reset();
    if (editing) {
      setConnectionId(editing.connectionId);
      setMode(editing.cron ? "cron" : "interval");
      setCron(editing.cron ?? "0 6 * * *");
      setIntervalSeconds(String(editing.intervalSeconds ?? 3600));
      setTimezone(editing.timezone ?? "UTC");
      const tpl = (editing.ingestionTemplate ?? {}) as Record<string, unknown>;
      setStatement(typeof tpl.statement === "string" ? tpl.statement : "");
      const nd = tpl.new_dataset as { name?: string } | undefined;
      setDatasetName(nd?.name ?? "");
      setOverlapPolicy(editing.overlapPolicy ?? "skip");
    } else {
      setConnectionId("");
      setMode("cron");
      setCron("0 6 * * *");
      setIntervalSeconds("3600");
      setTimezone("UTC");
      setStatement("");
      setDatasetName("");
      setOverlapPolicy("skip");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only on open/editing change
  }, [open, editing]);

  const submit = () => {
    setBanner(null);
    if (!editing && !connectionId) {
      setBanner(t("schedules.connectionRequired"));
      return;
    }
    if (!statement.trim()) {
      setBanner(t("schedules.statementRequired"));
      return;
    }
    if (!editing && !datasetName.trim()) {
      setBanner(t("schedules.datasetRequired"));
      return;
    }
    const interval = Number(intervalSeconds);
    if (mode === "interval" && (!Number.isFinite(interval) || interval < 60)) {
      setBanner(t("schedules.timingHint"));
      return;
    }
    // schedules.py _validate_template requires ingestion_mode="query" plus a
    // statement and a dataset target (live-verified 422 details otherwise).
    const ingestionTemplate: Record<string, unknown> = {
      ingestion_mode: "query",
      statement: statement.trim(),
    };
    if (editing) {
      // Preserve the pinned dataset_urn/new_dataset target on edit.
      const tpl = (editing.ingestionTemplate ?? {}) as Record<string, unknown>;
      if (tpl.dataset_urn) ingestionTemplate.dataset_urn = tpl.dataset_urn;
      if (tpl.new_dataset) ingestionTemplate.new_dataset = tpl.new_dataset;
      updateMutation.mutate(
        {
          id: editing.id,
          input: {
            ingestionTemplate,
            timezone,
            overlapPolicy,
            ...(mode === "cron" ? { cron } : { intervalSeconds: interval }),
          },
        },
        { onSuccess: () => onSaved(t("schedules.updated")) },
      );
    } else {
      ingestionTemplate.new_dataset = { name: datasetName.trim() };
      createMutation.mutate(
        {
          connectionId,
          ingestionTemplate,
          timezone,
          overlapPolicy,
          ...(mode === "cron" ? { cron } : { intervalSeconds: interval }),
        },
        { onSuccess: () => onSaved(t("schedules.created")) },
      );
    }
  };

  const error = (createMutation.error ?? updateMutation.error) as Error | null;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">
            {editing ? t("schedules.editTitle") : t("schedules.new")}
          </Dialog.Title>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            {!editing && (
              <div className="space-y-1.5">
                <Label htmlFor="sched-conn">{t("schedules.connection")}</Label>
                <select
                  id="sched-conn"
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  value={connectionId}
                  onChange={(e) => setConnectionId(e.target.value)}
                >
                  <option value="">{t("schedules.pickConnection")}</option>
                  {conns.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} ({c.connectorType})
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="space-y-1.5">
              <Label>{t("schedules.timing")}</Label>
              <div className="flex items-center gap-3 text-sm" role="radiogroup" aria-label={t("schedules.timing")}>
                <label className="flex items-center gap-1">
                  <input type="radio" checked={mode === "cron"} onChange={() => setMode("cron")} /> {t("schedules.cron")}
                </label>
                <label className="flex items-center gap-1">
                  <input type="radio" checked={mode === "interval"} onChange={() => setMode("interval")} />{" "}
                  {t("schedules.interval")}
                </label>
              </div>
              {mode === "cron" ? (
                <Input
                  aria-label={t("schedules.cron")}
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder={t("schedules.cronPlaceholder")}
                  className="font-mono"
                />
              ) : (
                <Input
                  aria-label={t("schedules.interval")}
                  type="number"
                  min={60}
                  value={intervalSeconds}
                  onChange={(e) => setIntervalSeconds(e.target.value)}
                />
              )}
              <p className="text-xs text-muted-foreground">{t("schedules.timingHint")}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sched-tz">{t("schedules.timezone")}</Label>
              <Input id="sched-tz" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sched-stmt">{t("schedules.statement")}</Label>
              <Textarea
                id="sched-stmt"
                rows={3}
                className="font-mono text-xs"
                value={statement}
                onChange={(e) => setStatement(e.target.value)}
                placeholder={t("schedules.statementPlaceholder")}
                spellCheck={false}
              />
            </div>
            {!editing && (
              <div className="space-y-1.5">
                <Label htmlFor="sched-ds">{t("schedules.newDatasetName")}</Label>
                <Input id="sched-ds" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
                <p className="text-xs text-muted-foreground">{t("schedules.newDatasetHint")}</p>
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="sched-overlap">{t("schedules.overlap")}</Label>
              <select
                id="sched-overlap"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={overlapPolicy}
                onChange={(e) => setOverlapPolicy(e.target.value)}
              >
                <option value="skip">{t("schedules.overlapSkip")}</option>
                <option value="buffer_one">{t("schedules.overlapBuffer")}</option>
              </select>
            </div>
            {banner && <p className="text-xs text-destructive">{banner}</p>}
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("action.cancel")}
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? t("schedules.creating") : editing ? t("action.save") : t("schedules.create")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
