"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { Workflow, ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  usePipelineRuns,
  usePipelineTemplates,
  useTerminatePipelineRun,
  useRetryPipelineRun,
  usePipelineRunManifest,
} from "@/lib/graphql/hooks";
import type { PipelineRun } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

// pipeline-orchestrator stores/filters status LOWERCASE and the filter is
// case-sensitive (live-verified: "SUCCEEDED" matches 0 runs, "succeeded"
// matches) — option values must stay lowercase, whatever the display casing.
const RUN_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const;

// enums.py: TERMINATABLE = {pending, quota_queued, submitted, running};
// retry requires failed. Compared lowercase to be casing-agnostic.
const TERMINATABLE = new Set(["pending", "quota_queued", "submitted", "running", "queued"]);

/**
 * Pipeline run history (pipeline-orchestrator GET /runs via bff `pipelineRuns`).
 * Read-only monitoring view: every run with its live status and timing; filter
 * by status. Template names are hydrated from the templates list client-side
 * (runs carry only templateId).
 */
export default function PipelineRunsPage() {
  const router = useRouter();
  const [status, setStatus] = useState("");
  const filter = useMemo(() => ({ status: status || undefined }), [status]);

  const query = usePipelineRuns(filter);
  // Task #78: "pipeline.run.status" was a list-wide subscription with no
  // matching broadcast scheme (routing is real and works — run-status:<urn> —
  // but only for one run at a time). Removed; usePipelineRuns's existing
  // polling fallback keeps this list fresh.
  const templatesQuery = usePipelineTemplates({});
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const templateName = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of templatesQuery.data?.pages ?? []) for (const tpl of p.nodes) m.set(tpl.id, tpl.name);
    return m;
  }, [templatesQuery.data]);

  const terminateMutation = useTerminatePipelineRun();
  const retryMutation = useRetryPipelineRun();
  const manifestMutation = usePipelineRunManifest();
  const [banner, setBanner] = useState<string | null>(null);
  const [toTerminate, setToTerminate] = useState<PipelineRun | null>(null);
  const [manifestOpen, setManifestOpen] = useState(false);

  const columns: Column<PipelineRun>[] = [
    {
      id: "pipeline",
      header: t("pipelines.runs.pipeline"),
      cell: (r) => (
        <span className="font-medium">
          {templateName.get(r.templateId) ?? <span className="font-mono text-xs">{r.templateId}</span>}
        </span>
      ),
    },
    {
      id: "status",
      header: t("pipelines.runs.status"),
      width: 130,
      cell: (r) =>
        r.status ? <StatusChip status={String(r.status)} /> : <span className="text-muted-foreground">—</span>,
    },
    { id: "created", header: t("pipelines.runs.created"), width: 170, cell: (r) => formatLocal(r.createdAt) },
    { id: "started", header: t("pipelines.runs.started"), width: 170, cell: (r) => formatLocal(r.startedAt) },
    { id: "finished", header: t("pipelines.runs.finished"), width: 170, cell: (r) => formatLocal(r.finishedAt) },
    {
      id: "actions",
      header: t("pipelines.runs.actions"),
      width: 250,
      cell: (r) => {
        const s = String(r.status ?? "").toLowerCase();
        return (
          <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
            <Can gate={FEATURE_GATES.viewPipelineRunManifest}>
              <Button
                variant="ghost"
                size="sm"
                disabled={manifestMutation.isPending}
                onClick={() => {
                  setManifestOpen(true);
                  manifestMutation.mutate(r.id);
                }}
              >
                {t("pipelines.runs.manifest")}
              </Button>
            </Can>
            {TERMINATABLE.has(s) && (
              <Can gate={FEATURE_GATES.terminatePipelineRun}>
                <Button variant="ghost" size="sm" disabled={terminateMutation.isPending} onClick={() => setToTerminate(r)}>
                  {t("pipelines.runs.terminate")}
                </Button>
              </Can>
            )}
            {s === "failed" && (
              <Can gate={FEATURE_GATES.retryPipelineRun}>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={retryMutation.isPending}
                  onClick={() =>
                    retryMutation.mutate(r.id, {
                      onSuccess: () => setBanner(t("pipelines.runs.retried")),
                      onError: (e) => setBanner((e as Error).message),
                    })
                  }
                >
                  {t("pipelines.runs.retry")}
                </Button>
              </Can>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <PageHeader
        title={t("pipelines.runs.title")}
        description={t("pipelines.runs.subtitle")}
        actions={
          <Button variant="outline" onClick={() => router.push("/data/pipelines")}>
            <ArrowLeft /> {t("pipelines.back")}
          </Button>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-sm">
          <span className="text-muted-foreground">{t("pipelines.runs.filterStatus")}</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by run status"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="">all</option>
            {RUN_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        {status && (
          <Button variant="ghost" size="sm" onClick={() => setStatus("")}>
            Clear
          </Button>
        )}
      </div>

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="notice-banner">
          {banner}
        </div>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("pipelines.runs.empty")}
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("pipelines.runs.title")}
          rows={rows}
          columns={columns}
          rowId={(r) => r.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Workflow className="size-8" />
              <p>{t("pipelines.runs.emptyHint")}</p>
            </div>
          }
        />
      </AsyncBoundary>

      <ConfirmDialog
        open={!!toTerminate}
        onOpenChange={(o) => !o && setToTerminate(null)}
        title={t("pipelines.runs.terminate")}
        description={t("pipelines.runs.terminateConfirm")}
        confirmLabel={t("pipelines.runs.terminate")}
        destructive
        onConfirm={() => {
          if (toTerminate)
            terminateMutation.mutate(toTerminate.id, {
              onSuccess: () => setBanner(t("pipelines.runs.terminated")),
              onError: (e) => setBanner((e as Error).message),
              onSettled: () => setToTerminate(null),
            });
        }}
      />

      {/* Compiled manifest + resolved parameters (GET /runs/{id}/manifest). */}
      <Dialog.Root open={manifestOpen} onOpenChange={(o) => { setManifestOpen(o); if (!o) manifestMutation.reset(); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
          <Dialog.Content
            className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
            aria-describedby={undefined}
          >
            <Dialog.Title className="text-lg font-semibold">{t("pipelines.runs.manifestTitle")}</Dialog.Title>
            <div className="mt-4 space-y-4">
              {manifestMutation.isPending && <p className="text-sm text-muted-foreground">{t("state.loading")}</p>}
              {manifestMutation.isError && (
                <p role="alert" className="text-sm text-destructive">
                  {(manifestMutation.error as Error).message}
                </p>
              )}
              {manifestMutation.data && (
                <>
                  <div>
                    <p className="mb-1 text-sm font-medium">{t("pipelines.runs.manifestParams")}</p>
                    <pre className="max-h-40 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs" data-testid="run-manifest-params">
                      {JSON.stringify(manifestMutation.data.resolvedParameters ?? null, null, 2)}
                    </pre>
                  </div>
                  <pre className="max-h-96 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs" data-testid="run-manifest">
                    {JSON.stringify(manifestMutation.data.manifest ?? null, null, 2)}
                  </pre>
                </>
              )}
            </div>
            <div className="mt-4 flex justify-end">
              <Button variant="outline" onClick={() => setManifestOpen(false)}>
                {t("semantic.version.close")}
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
