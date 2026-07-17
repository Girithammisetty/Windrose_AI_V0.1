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
  usePipelineTemplates,
  usePipelineSchedules,
  useCreatePipelineSchedule,
  useDeletePipelineSchedule,
  usePausePipelineSchedule,
  useResumePipelineSchedule,
  useRunNowPipelineSchedule,
} from "@/lib/graphql/hooks";
import type { PipelineSchedule, PipelineTemplate } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/**
 * Recurring pipeline schedules (pipeline-orchestrator /pipeline-schedules,
 * PIPE-FR-050): fire a pipeline template's active version on a cron cadence,
 * with pause/resume/run-now and delete. Template names hydrate from the
 * templates list (schedules carry only templateId).
 */
export function PipelineSchedulesPanel({ onNotice }: { onNotice: (msg: string) => void }) {
  const query = usePipelineSchedules();
  const templatesQuery = usePipelineTemplates({});
  const rows = query.data ?? [];
  const templateName = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of templatesQuery.data?.pages ?? []) for (const tpl of p.nodes) m.set(tpl.id, tpl.name);
    return m;
  }, [templatesQuery.data]);

  const pauseMutation = usePausePipelineSchedule();
  const resumeMutation = useResumePipelineSchedule();
  const runNowMutation = useRunNowPipelineSchedule();
  const deleteMutation = useDeletePipelineSchedule();

  const [formOpen, setFormOpen] = useState(false);
  const [toDelete, setToDelete] = useState<PipelineSchedule | null>(null);

  const columns: Column<PipelineSchedule>[] = [
    {
      id: "name",
      header: t("pipelines.schedules.name"),
      cell: (s) => <span className="font-medium">{s.name ?? <span className="text-muted-foreground">—</span>}</span>,
    },
    {
      id: "template",
      header: t("pipelines.schedules.template"),
      width: 180,
      cell: (s) => (
        <span className="font-medium">
          {templateName.get(s.templateId) ?? <span className="font-mono text-xs">{s.templateId.slice(0, 8)}</span>}
        </span>
      ),
    },
    {
      id: "timing",
      header: t("pipelines.schedules.timing"),
      cell: (s) => (
        <span className="font-mono text-xs">
          {s.cron}
          {s.timezone && s.timezone !== "UTC" ? ` (${s.timezone})` : ""}
        </span>
      ),
    },
    {
      id: "status",
      header: t("pipelines.schedules.status"),
      width: 110,
      cell: (s) => (
        <Badge variant={s.enabled ? "success" : "secondary"}>{s.enabled ? "enabled" : "paused"}</Badge>
      ),
    },
    { id: "nextFire", header: t("pipelines.schedules.nextFire"), width: 160, cell: (s) => formatLocal(s.nextFireAt) },
    {
      id: "actions",
      header: t("pipelines.schedules.actions"),
      width: 280,
      cell: (s) => (
        <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          <Can gate={FEATURE_GATES.runPipelineScheduleNow}>
            <Button
              variant="outline"
              size="sm"
              disabled={runNowMutation.isPending || !s.enabled}
              onClick={() =>
                runNowMutation.mutate(s.id, {
                  onSuccess: () => onNotice(t("pipelines.schedules.fired")),
                  onError: (e) => onNotice((e as Error).message),
                })
              }
            >
              {t("pipelines.schedules.runNow")}
            </Button>
          </Can>
          <Can gate={FEATURE_GATES.updatePipelineSchedule}>
            {s.enabled ? (
              <Button
                variant="ghost"
                size="sm"
                disabled={pauseMutation.isPending}
                onClick={() =>
                  pauseMutation.mutate(s.id, {
                    onSuccess: () => onNotice(t("pipelines.schedules.paused")),
                    onError: (e) => onNotice((e as Error).message),
                  })
                }
              >
                {t("pipelines.schedules.pause")}
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                disabled={resumeMutation.isPending}
                onClick={() =>
                  resumeMutation.mutate(s.id, {
                    onSuccess: () => onNotice(t("pipelines.schedules.resumed")),
                    onError: (e) => onNotice((e as Error).message),
                  })
                }
              >
                {t("pipelines.schedules.resume")}
              </Button>
            )}
          </Can>
          <Can gate={FEATURE_GATES.deletePipelineSchedule}>
            <Button variant="ghost" size="sm" onClick={() => setToDelete(s)}>
              {t("pipelines.schedules.delete")}
            </Button>
          </Can>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{t("pipelines.schedules.subtitle")}</p>
        <Can gate={FEATURE_GATES.createPipelineSchedule}>
          <Button size="sm" onClick={() => setFormOpen(true)}>
            <Plus /> {t("pipelines.schedules.new")}
          </Button>
        </Can>
      </div>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("pipelines.schedules.empty")}
        emptyCta={
          <Can gate={FEATURE_GATES.createPipelineSchedule}>
            <Button variant="outline" size="sm" className="mt-2" onClick={() => setFormOpen(true)}>
              <Plus /> {t("pipelines.schedules.new")}
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("pipelines.schedules.title")}
          rows={rows}
          columns={columns}
          rowId={(s) => s.id}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <CalendarClock className="size-8" />
              <p>{t("pipelines.schedules.emptyHint")}</p>
            </div>
          }
        />
      </AsyncBoundary>

      <ScheduleDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        templates={templatesQuery.data?.pages.flatMap((p) => p.nodes) ?? []}
        onSaved={(msg) => {
          setFormOpen(false);
          onNotice(msg);
        }}
      />

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t("pipelines.schedules.delete")}
        description={t("pipelines.schedules.deleteConfirm")}
        confirmLabel={t("pipelines.schedules.delete")}
        destructive
        onConfirm={() => {
          if (toDelete)
            deleteMutation.mutate(toDelete.id, {
              onSuccess: () => onNotice(t("pipelines.schedules.deleted")),
              onError: (e) => onNotice((e as Error).message),
              onSettled: () => setToDelete(null),
            });
        }}
      />
    </div>
  );
}

/** Create form: pick a template, a cron expression, timezone, optional run
 * parameters (JSON). Run-parameter JSON is validated client-side so a malformed
 * body is caught before the mutation. */
function ScheduleDialog({
  open,
  onOpenChange,
  templates,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  templates: PipelineTemplate[];
  onSaved: (msg: string) => void;
}) {
  const createMutation = useCreatePipelineSchedule();
  const pending = createMutation.isPending;

  const [templateId, setTemplateId] = useState("");
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 2 * * *");
  const [timezone, setTimezone] = useState("UTC");
  const [runParams, setRunParams] = useState("");
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setBanner(null);
    createMutation.reset();
    setTemplateId("");
    setName("");
    setCron("0 2 * * *");
    setTimezone("UTC");
    setRunParams("");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only on open change
  }, [open]);

  const submit = () => {
    setBanner(null);
    if (!templateId) {
      setBanner(t("pipelines.schedules.templateRequired"));
      return;
    }
    if (!cron.trim()) {
      setBanner(t("pipelines.schedules.cronRequired"));
      return;
    }
    let parsedParams: Record<string, unknown> | undefined;
    if (runParams.trim()) {
      try {
        parsedParams = JSON.parse(runParams) as Record<string, unknown>;
      } catch {
        setBanner(t("pipelines.schedules.runParamsInvalid"));
        return;
      }
    }
    createMutation.mutate(
      {
        templateId,
        name: name.trim() || undefined,
        cron: cron.trim(),
        timezone: timezone.trim() || undefined,
        runParameters: parsedParams,
      },
      { onSuccess: () => onSaved(t("pipelines.schedules.created")) },
    );
  };

  const error = createMutation.error as Error | null;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">{t("pipelines.schedules.new")}</Dialog.Title>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="psched-tpl">{t("pipelines.schedules.template")}</Label>
              <select
                id="psched-tpl"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
              >
                <option value="">{t("pipelines.schedules.pickTemplate")}</option>
                {templates
                  .filter((tpl) => !tpl.archived)
                  .map((tpl) => (
                    <option key={tpl.id} value={tpl.id}>
                      {tpl.name} ({tpl.pipelineType})
                    </option>
                  ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="psched-name">{t("pipelines.schedules.name")}</Label>
              <Input id="psched-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="psched-cron">{t("pipelines.schedules.cron")}</Label>
              <Input
                id="psched-cron"
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                placeholder={t("pipelines.schedules.cronPlaceholder")}
                className="font-mono"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="psched-tz">{t("pipelines.schedules.timezone")}</Label>
              <Input id="psched-tz" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="psched-params">{t("pipelines.schedules.runParams")}</Label>
              <Textarea
                id="psched-params"
                rows={3}
                className="font-mono text-xs"
                value={runParams}
                onChange={(e) => setRunParams(e.target.value)}
                placeholder='{ "label_column": "target" }'
                spellCheck={false}
              />
              <p className="text-xs text-muted-foreground">{t("pipelines.schedules.runParamsHint")}</p>
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
                {pending ? t("pipelines.schedules.creating") : t("pipelines.schedules.create")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
