"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Workflow, Plus, Loader2, CalendarClock } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  usePipelineTemplates,
  useRunPipeline,
  useClonePipelineTemplate,
  useCompilePipelineTemplate,
  useDeletePipelineTemplate,
  useRestorePipelineTemplate,
} from "@/lib/graphql/hooks";
import { TemplateVersionsDialog, CompiledManifestDialog } from "@/components/pipelines/TemplateLifecycle";
import type { PipelineTemplate } from "@/lib/graphql/types";
import { PIPELINE_TYPES } from "@/lib/pipelines/form";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/** Maps a bff validationStatus string onto a StatusChip lifecycle state. */
function statusToChip(s?: string | null): string | null {
  if (!s) return null;
  const up = s.toUpperCase();
  if (up === "VALID" || up === "PASSED") return "SUCCEEDED";
  if (up === "INVALID" || up === "FAILED") return "FAILED";
  if (up === "PENDING" || up === "DRAFT") return "PENDING";
  return up;
}

export default function PipelinesPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [pipelineType, setPipelineType] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const filter = useMemo(
    () => ({
      q: q || undefined,
      pipelineType: pipelineType || undefined,
      includeArchived: showArchived || undefined,
    }),
    [q, pipelineType, showArchived],
  );

  const query = usePipelineTemplates(filter);
  const runMutation = useRunPipeline();
  const cloneMutation = useClonePipelineTemplate();
  const compileMutation = useCompilePipelineTemplate();
  const deleteMutation = useDeletePipelineTemplate();
  const restoreMutation = useRestorePipelineTemplate();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const [runningId, setRunningId] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [versionsFor, setVersionsFor] = useState<PipelineTemplate | null>(null);
  const [toArchive, setToArchive] = useState<PipelineTemplate | null>(null);
  const [compileOpen, setCompileOpen] = useState(false);

  const onRun = (tpl: PipelineTemplate) => {
    setRunningId(tpl.id);
    setBanner(null);
    runMutation.mutate(
      { id: tpl.id },
      {
        onSuccess: (r) => setBanner(`${tpl.name}: ${t("pipelines.runStarted", { status: String(r.runPipeline.status ?? "QUEUED") })}`),
        onError: (e) => setBanner(`${tpl.name}: ${e.message}`),
        onSettled: () => setRunningId(null),
      },
    );
  };

  const columns: Column<PipelineTemplate>[] = [
    {
      id: "name",
      header: t("pipelines.name"),
      cell: (p) => (
        <span className="flex items-center gap-2 font-medium">
          {p.name}
          {p.archived && <Badge variant="secondary">archived</Badge>}
          {p.isSystem && <Badge variant="outline">system</Badge>}
        </span>
      ),
    },
    { id: "type", header: t("pipelines.type"), width: 150, cell: (p) => p.pipelineType },
    {
      id: "status",
      header: t("pipelines.status"),
      width: 130,
      cell: (p) =>
        statusToChip(p.validationStatus) ? (
          <StatusChip status={statusToChip(p.validationStatus)} />
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    { id: "created", header: t("pipelines.created"), width: 170, cell: (p) => formatLocal(p.createdAt) },
    {
      id: "actions",
      header: t("pipelines.actions"),
      width: 380,
      cell: (p) => (
        <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          {p.archived ? (
            <Can gate={FEATURE_GATES.updatePipelineTemplate}>
              <Button
                variant="outline"
                size="sm"
                disabled={restoreMutation.isPending}
                onClick={() =>
                  restoreMutation.mutate(p.id, {
                    onSuccess: () => setBanner(`${p.name}: ${t("pipelines.restored")}`),
                    onError: (e) => setBanner(`${p.name}: ${e.message}`),
                  })
                }
              >
                {t("pipelines.restore")}
              </Button>
            </Can>
          ) : (
            <>
              <Can gate={FEATURE_GATES.buildPipeline}>
                <Button variant="outline" size="sm" onClick={() => onRun(p)} disabled={runningId === p.id}>
                  {runningId === p.id ? <Loader2 className="animate-spin" /> : t("pipelines.run")}
                </Button>
              </Can>
              {/* System templates cannot be edited (they 409 on archive/mutation); hide it. */}
              {!p.isSystem && (
                <Can gate={FEATURE_GATES.updatePipelineTemplate}>
                  <Button variant="ghost" size="sm" onClick={() => router.push(`/data/pipelines/${p.id}/edit`)}>
                    {t("pipelines.edit")}
                  </Button>
                </Can>
              )}
              <Button variant="ghost" size="sm" onClick={() => setVersionsFor(p)}>
                {t("pipelines.versions")}
              </Button>
              <Can gate={FEATURE_GATES.compilePipelineTemplate}>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={compileMutation.isPending}
                  onClick={() => {
                    setCompileOpen(true);
                    compileMutation.mutate(p.id);
                  }}
                >
                  {t("pipelines.compile")}
                </Button>
              </Can>
              <Can gate={FEATURE_GATES.clonePipelineTemplate}>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={cloneMutation.isPending}
                  onClick={() =>
                    cloneMutation.mutate(p.id, {
                      onSuccess: () => setBanner(`${p.name}: ${t("pipelines.cloned")}`),
                      onError: (e) => setBanner(`${p.name}: ${e.message}`),
                    })
                  }
                >
                  {t("pipelines.clone")}
                </Button>
              </Can>
              {/* System templates cannot be archived — the service 409s; hide the control. */}
              {!p.isSystem && (
                <Can gate={FEATURE_GATES.deletePipelineTemplate}>
                  <Button variant="ghost" size="sm" onClick={() => setToArchive(p)}>
                    {t("pipelines.archive")}
                  </Button>
                </Can>
              )}
            </>
          )}
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t("pipelines.title")}
        description={t("pipelines.subtitle")}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => router.push("/data/pipelines/runs")}>
              <Workflow /> {t("pipelines.runs.view")}
            </Button>
            <Can gate={FEATURE_GATES.viewPipelineSchedules}>
              <Button variant="outline" onClick={() => router.push("/data/pipelines/schedules")}>
                <CalendarClock /> {t("pipelines.schedules.view")}
              </Button>
            </Can>
            <Can gate={FEATURE_GATES.buildPipeline}>
              <Button onClick={() => router.push("/data/pipelines/new")}>
                <Plus /> {t("pipelines.new")}
              </Button>
            </Can>
          </div>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          placeholder={t("pipelines.search")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-xs"
          aria-label={t("pipelines.search")}
        />
        <label className="flex items-center gap-1 text-sm">
          <span className="text-muted-foreground">{t("pipelines.type")}</span>
          <select
            value={pipelineType}
            onChange={(e) => setPipelineType(e.target.value)}
            aria-label="Filter by pipeline type"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="">all</option>
            {PIPELINE_TYPES.map((pt) => (
              <option key={pt} value={pt}>
                {pt}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
            aria-label={t("pipelines.showArchived")}
            className="size-4 accent-[hsl(var(--primary))]"
          />
          <span className="text-muted-foreground">{t("pipelines.showArchived")}</span>
        </label>
        {(q || pipelineType) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setQ("");
              setPipelineType("");
            }}
          >
            Clear
          </Button>
        )}
      </div>

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="run-banner">
          {banner}
        </div>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("pipelines.empty")}
        emptyCta={
          <Can gate={FEATURE_GATES.buildPipeline}>
            <Button className="mt-2" onClick={() => router.push("/data/pipelines/new")}>
              <Plus /> {t("pipelines.new")}
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("pipelines.title")}
          rows={rows}
          columns={columns}
          rowId={(p) => p.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Workflow className="size-8" />
              <p>{t("pipelines.emptyHint")}</p>
            </div>
          }
        />
      </AsyncBoundary>

      <TemplateVersionsDialog
        template={versionsFor}
        onOpenChange={(o) => !o && setVersionsFor(null)}
        onNotice={setBanner}
      />

      <CompiledManifestDialog
        open={compileOpen}
        onOpenChange={(o) => {
          setCompileOpen(o);
          if (!o) compileMutation.reset();
        }}
        result={compileMutation.data ?? null}
        isPending={compileMutation.isPending}
        error={compileMutation.error as Error | null}
      />

      <ConfirmDialog
        open={!!toArchive}
        onOpenChange={(o) => !o && setToArchive(null)}
        title={t("pipelines.archive")}
        description={toArchive ? t("pipelines.archiveConfirm", { name: toArchive.name }) : ""}
        confirmLabel={t("pipelines.archive")}
        destructive
        onConfirm={() => {
          if (toArchive)
            deleteMutation.mutate(toArchive.id, {
              onSuccess: () => setBanner(`${toArchive.name}: ${t("pipelines.archived")}`),
              onError: (e) => setBanner(`${toArchive.name}: ${e.message}`),
              onSettled: () => setToArchive(null),
            });
        }}
      />
    </div>
  );
}
