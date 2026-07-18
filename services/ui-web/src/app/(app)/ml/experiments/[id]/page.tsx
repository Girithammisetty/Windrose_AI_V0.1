"use client";
import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { Bot } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, Input, Label, Textarea } from "@/components/ui/primitives";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useExperiment,
  // Tier 4b: ml ops — best run / compare / experiment edit.
  useBestRun,
  useCompareRuns,
  useUpdateExperiment,
  useArchiveExperiment,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { useHubTopics } from "@/lib/realtime/useHubTopics";
import type { Experiment, Run } from "@/lib/graphql/types";

export default function ExperimentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useExperiment(id);
  const router = useRouter();
  const exp = query.data?.experiment;

  const rows = useMemo(() => exp?.runs.nodes ?? [], [exp]);
  // Task #78: "run.status" wasn't a valid topic. Each row here is a real,
  // known run — subscribe to each one's own run-status:<run-urn> (experiment-
  // service's run.* events carry resource_urn = the run's own URN; see
  // routing.go's "experiment_run" rule). Not a list-broadcast: every topic is
  // a concrete resource this page already knows about.
  const runTopics = useMemo(() => rows.map((r) => `run-status:${r.urn}`), [rows]);
  useHubTopics(runTopics);

  const [editOpen, setEditOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const archive = useArchiveExperiment();
  // Run comparison selection (local — the runs table is small and page-scoped).
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [compareOpen, setCompareOpen] = useState(false);
  const toggle = (runId: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });

  // No Metrics column here: the runs-LIST payload carries no metrics (only the
  // run detail does) — open a run to see its metrics rather than an empty column.
  const columns: Column<Run>[] = [
    { id: "name", header: "Run", width: "2fr", cell: (r) => <span className="font-medium">{r.name ?? r.urn}</span> },
    { id: "status", header: "Status", width: 130, cell: (r) => <StatusChip status={r.status} live /> },
    { id: "stage", header: "Model stage", width: 140, cell: (r) => r.model?.stage ?? "—" },
  ];

  return (
    <div>
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && !exp}
        emptyTitle="Experiment not found"
        onRetry={() => query.refetch()}
      >
        {exp && (
          <>
            <PageHeader
              title={exp.name}
              description={exp.description ?? undefined}
              actions={
                <div className="flex items-center gap-2">
                  <Can gate={FEATURE_GATES.updateExperiment}>
                    <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
                      Edit
                    </Button>
                  </Can>
                  {!exp.archived && (
                    <Can gate={FEATURE_GATES.archiveExperiment}>
                      <Button variant="outline" size="sm" onClick={() => setArchiveOpen(true)}>
                        Archive
                      </Button>
                    </Can>
                  )}
                </div>
              }
            />

            <BestRunCard experimentId={id} runs={rows} />

            <div className="mb-2 mt-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold">Runs</h2>
              <Button
                variant="outline"
                size="sm"
                disabled={selected.size < 2}
                onClick={() => setCompareOpen(true)}
              >
                Compare ({selected.size})
              </Button>
            </div>
            <DataTable
              ariaLabel="Runs"
              rows={rows}
              columns={columns}
              rowId={(r) => r.id}
              selectable
              selectedIds={selected}
              onToggle={toggle}
              onRowActivate={(r) => router.push(`/ml/runs/${r.id}`)}
              emptyState={
                <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
                  <Bot className="size-8" />
                  <p>No runs in this experiment</p>
                </div>
              }
            />

            <CompareRunsDialog
              open={compareOpen}
              onOpenChange={setCompareOpen}
              runIds={[...selected]}
              runs={rows}
            />

            <EditExperimentDialog open={editOpen} onOpenChange={setEditOpen} experiment={exp} />

            <ConfirmDialog
              open={archiveOpen}
              onOpenChange={setArchiveOpen}
              title="Archive experiment?"
              description="Archiving hides the experiment from the active list. It can be restored later."
              confirmLabel={archive.isPending ? "Archiving…" : "Archive"}
              destructive
              onConfirm={() => {
                if (archive.isPending) return;
                archive.mutate(exp.id, {
                  onSuccess: () => {
                    setArchiveOpen(false);
                    router.push("/ml/experiments");
                  },
                });
              }}
            />
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}

/**
 * Best run by one metric (experiment-service GET .../runs/best). Metric options
 * derive from the metric keys the loaded runs actually carry — when the list
 * payload has none (it usually serializes no metrics), a free-text metric input
 * is offered instead so the real backend capability stays reachable.
 */
function BestRunCard({ experimentId, runs }: { experimentId: string; runs: Run[] }) {
  const metricOptions = useMemo(() => {
    const keys = new Set<string>();
    for (const r of runs) {
      if (r.metrics && typeof r.metrics === "object" && !Array.isArray(r.metrics)) {
        for (const k of Object.keys(r.metrics as Record<string, unknown>)) keys.add(k);
      }
    }
    return [...keys].sort();
  }, [runs]);

  const [metric, setMetric] = useState("");
  const [direction, setDirection] = useState<"max" | "min">("max");
  const [asked, setAsked] = useState(false);
  const best = useBestRun(experimentId, { metric: metric.trim(), direction }, asked && !!metric.trim());

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Best run</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {runs.length === 0 ? (
          <p className="text-muted-foreground">No runs yet — the best-run lookup needs at least one run with the metric.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="best-metric">Metric</Label>
                {metricOptions.length > 0 ? (
                  <select
                    id="best-metric"
                    className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                    value={metric}
                    onChange={(e) => {
                      setMetric(e.target.value);
                      setAsked(false);
                    }}
                  >
                    <option value="">Pick a metric…</option>
                    {metricOptions.map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
                  </select>
                ) : (
                  // The runs LIST carries no metric payloads — offer the real
                  // metric key by name (the service matches on logged keys).
                  <Input
                    id="best-metric"
                    className="h-9 w-48"
                    value={metric}
                    onChange={(e) => {
                      setMetric(e.target.value);
                      setAsked(false);
                    }}
                    placeholder="e.g. f1"
                  />
                )}
              </div>
              <div className="space-y-1.5">
                <Label>Direction</Label>
                <div className="flex items-center gap-3" role="radiogroup" aria-label="Direction">
                  <label className="flex items-center gap-1">
                    <input
                      type="radio"
                      checked={direction === "max"}
                      onChange={() => {
                        setDirection("max");
                        setAsked(false);
                      }}
                    />{" "}
                    max
                  </label>
                  <label className="flex items-center gap-1">
                    <input
                      type="radio"
                      checked={direction === "min"}
                      onChange={() => {
                        setDirection("min");
                        setAsked(false);
                      }}
                    />{" "}
                    min
                  </label>
                </div>
              </div>
              <Button size="sm" disabled={!metric.trim()} onClick={() => setAsked(true)}>
                Find best run
              </Button>
            </div>

            {asked && (
              <AsyncBoundary
                isLoading={best.isLoading}
                isError={best.isError}
                error={best.error}
                isEmpty={!best.isLoading && !best.data}
                emptyTitle={`No run in this experiment has the metric "${metric.trim()}".`}
                onRetry={() => best.refetch()}
              >
                {best.data && (
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border p-3" data-testid="best-run-result">
                    <Link href={`/ml/runs/${best.data.id}`} className="font-medium text-primary hover:underline">
                      {best.data.name ?? best.data.id}
                    </Link>
                    <StatusChip status={best.data.status} />
                    <span className="font-mono text-xs">
                      {metric.trim()} ={" "}
                      {(best.data.metrics as Record<string, number> | null)?.[metric.trim()] ?? "—"}
                    </span>
                  </div>
                )}
              </AsyncBoundary>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Side-by-side comparison of the selected runs (experiment-service POST
 * /runs/compare): one column per run, a row per metric then per param — real
 * values (or —) straight from the compare matrix.
 */
function CompareRunsDialog({
  open,
  onOpenChange,
  runIds,
  runs,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  runIds: string[];
  runs: Run[];
}) {
  const compare = useCompareRuns(runIds, open);
  const nameOf = (id: string) => runs.find((r) => r.id === id)?.name ?? id;
  const orderedIds = compare.data?.runIds ?? runIds;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">Compare runs ({runIds.length})</Dialog.Title>
          <div className="mt-4">
            <AsyncBoundary
              isLoading={compare.isLoading}
              isError={compare.isError}
              error={compare.error}
              isEmpty={
                !compare.isLoading &&
                (compare.data?.metrics?.length ?? 0) === 0 &&
                (compare.data?.params?.length ?? 0) === 0
              }
              emptyTitle="The selected runs share no logged metrics or params."
              onRetry={() => compare.refetch()}
            >
              {compare.data && (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm" aria-label="Run comparison">
                    <thead>
                      <tr className="border-b text-left text-xs text-muted-foreground">
                        <th className="py-2 pr-4 font-medium">Key</th>
                        {orderedIds.map((rid) => (
                          <th key={rid} className="py-2 pr-4 font-medium">
                            <Link href={`/ml/runs/${rid}`} className="text-primary hover:underline">
                              {nameOf(rid)}
                            </Link>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(compare.data.metrics ?? []).map((m) => (
                        <tr key={`metric-${m.key}`} className="border-b last:border-0">
                          <td className="py-1.5 pr-4 font-mono text-xs">
                            {m.key}
                            <span className="ml-1 text-muted-foreground">({m.direction ?? "max"})</span>
                          </td>
                          {orderedIds.map((rid) => (
                            <td
                              key={rid}
                              className={`py-1.5 pr-4 tabular-nums ${m.best_run_id === rid ? "font-semibold text-[hsl(var(--success))]" : ""}`}
                            >
                              {m.values?.[rid] ?? "—"}
                            </td>
                          ))}
                        </tr>
                      ))}
                      {(compare.data.params ?? []).map((p) => (
                        <tr key={`param-${p.key}`} className="border-b last:border-0">
                          <td className="py-1.5 pr-4 font-mono text-xs">
                            {p.key}
                            {p.differs && <span className="ml-1 text-[hsl(var(--warning))]">differs</span>}
                          </td>
                          {orderedIds.map((rid) => (
                            <td key={rid} className="py-1.5 pr-4">
                              {p.values?.[rid] ?? "—"}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </AsyncBoundary>
          </div>
          <div className="mt-4 flex justify-end">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Edit name/description (experiment-service PATCH /experiments/{id}) —
 * gated on experiment.experiment.update. */
function EditExperimentDialog({
  open,
  onOpenChange,
  experiment,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  experiment: Experiment;
}) {
  const update = useUpdateExperiment();
  const [name, setName] = useState(experiment.name);
  const [description, setDescription] = useState(experiment.description ?? "");
  const error = update.error instanceof GraphQLRequestError ? update.error : (update.error as Error | null);

  useEffect(() => {
    if (!open) return;
    update.reset();
    setName(experiment.name);
    setDescription(experiment.description ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the dialog opens
  }, [open, experiment]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">Edit experiment</Dialog.Title>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim()) return;
              update.mutate(
                { id: experiment.id, input: { name: name.trim(), description: description.trim() } },
                { onSuccess: () => onOpenChange(false) },
              );
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="exp-name">Name</Label>
              <Input id="exp-name" value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="exp-description">Description</Label>
              <Textarea
                id="exp-description"
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={update.isPending || !name.trim()}>
                {update.isPending ? "Saving…" : "Save"}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
