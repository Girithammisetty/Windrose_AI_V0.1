"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import * as Dialog from "@radix-ui/react-dialog";
import { CalendarClock, Plus } from "lucide-react";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardHeader, CardTitle, Input, Label } from "@/components/ui/primitives";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useDatasets,
  useModels,
  useModel,
  useInferenceSchedules,
  useInferenceScheduleFires,
  useCreateInferenceSchedule,
  useUpdateInferenceSchedule,
  useDeleteInferenceSchedule,
  usePauseInferenceSchedule,
  useResumeInferenceSchedule,
  useTriggerInferenceSchedule,
} from "@/lib/graphql/hooks";
import { inferenceStatusUi } from "@/lib/inference-status";
import type { InferenceSchedule } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

/**
 * Recurring scoring schedules (inference-service /schedules, INF-FR-050..055):
 * cron or fixed-interval scoring of one dataset with a pinned model version OR
 * a stage-resolved model, with pause/resume/trigger-now, edit (PATCH-able
 * fields only), delete and a per-schedule fire history. Mirrors the ingestion
 * SchedulesPanel UX.
 */
export function InferenceSchedulesPanel() {
  const query = useInferenceSchedules();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const pauseMutation = usePauseInferenceSchedule();
  const resumeMutation = useResumeInferenceSchedule();
  const triggerMutation = useTriggerInferenceSchedule();
  const deleteMutation = useDeleteInferenceSchedule();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<InferenceSchedule | null>(null);
  const [toDelete, setToDelete] = useState<InferenceSchedule | null>(null);
  const [firesFor, setFiresFor] = useState<InferenceSchedule | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const columns: Column<InferenceSchedule>[] = [
    {
      id: "name",
      header: "Schedule",
      width: "1.5fr",
      cell: (s) => <span className="truncate font-medium">{s.name ?? s.id}</span>,
    },
    {
      id: "timing",
      header: "Timing",
      width: "1fr",
      cell: (s) => (
        <span className="font-mono text-xs">
          {s.cron ?? (s.intervalSeconds != null ? `every ${s.intervalSeconds}s` : "—")}
          {s.timezone && s.timezone !== "UTC" ? ` (${s.timezone})` : ""}
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      width: 150,
      cell: (s) =>
        s.enabled ? (
          <Badge variant="success">enabled</Badge>
        ) : (
          <span className="flex items-center gap-1">
            <Badge variant="secondary">paused</Badge>
            {s.pausedReason && (
              <span className="truncate text-xs text-muted-foreground" title={s.pausedReason}>
                {s.pausedReason}
              </span>
            )}
          </span>
        ),
    },
    {
      id: "model",
      header: "Model",
      width: "1.5fr",
      cell: (s) =>
        s.modelVersionUrn ? (
          <span className="truncate font-mono text-xs" title={s.modelVersionUrn}>
            {s.modelVersionUrn}
          </span>
        ) : (
          <span className="text-xs">
            <span className="font-mono">{s.modelUrn ?? "—"}</span>
            {s.stageSelector && <Badge variant="secondary" className="ml-1">{s.stageSelector}</Badge>}
          </span>
        ),
    },
    {
      id: "failures",
      header: "Failures",
      width: 90,
      cell: (s) => (
        <span className={s.consecutiveFailures ? "font-medium text-destructive" : ""}>
          {s.consecutiveFailures ?? 0}
        </span>
      ),
    },
    { id: "nextFire", header: "Next fire", width: 160, cell: (s) => formatLocal(s.nextFireAt) },
    {
      id: "actions",
      header: "Actions",
      width: 340,
      cell: (s) => (
        <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          <Can gate={FEATURE_GATES.updateInferenceSchedule}>
            <Button
              variant="outline"
              size="sm"
              disabled={triggerMutation.isPending}
              onClick={() =>
                triggerMutation.mutate(s.id, {
                  onSuccess: (r) => {
                    const res = r as { fired?: boolean; job_id?: string; reason?: string; error?: string } | null;
                    setNotice(
                      res?.fired
                        ? `Fired — job ${res.job_id ?? "created"}.`
                        : `Fire skipped: ${res?.reason ?? "unknown"}${res?.error ? ` (${res.error})` : ""}`,
                    );
                  },
                  onError: (e) => setNotice((e as Error).message),
                })
              }
            >
              Trigger now
            </Button>
            {s.enabled ? (
              <Button
                variant="ghost"
                size="sm"
                disabled={pauseMutation.isPending}
                onClick={() => pauseMutation.mutate(s.id, { onSuccess: () => setNotice("Schedule paused.") })}
              >
                Pause
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                disabled={resumeMutation.isPending}
                onClick={() => resumeMutation.mutate(s.id, { onSuccess: () => setNotice("Schedule resumed.") })}
              >
                Resume
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={() => { setEditing(s); setFormOpen(true); }}>
              Edit
            </Button>
          </Can>
          <Can gate={FEATURE_GATES.readInferenceSchedules}>
            <Button variant="ghost" size="sm" onClick={() => setFiresFor(s)}>
              Fires
            </Button>
          </Can>
          <Can gate={FEATURE_GATES.deleteInferenceSchedule}>
            <Button variant="ghost" size="sm" onClick={() => setToDelete(s)}>
              Delete
            </Button>
          </Can>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Recurring scoring: a model (pinned version or resolved by stage at fire time) over a dataset, on a cron or interval.
        </p>
        <Can gate={FEATURE_GATES.createInferenceSchedule}>
          <Button size="sm" onClick={() => { setEditing(null); setFormOpen(true); }}>
            <Plus /> New schedule
          </Button>
        </Can>
      </div>

      {notice && (
        <p role="status" className="mb-2 text-xs text-muted-foreground">
          {notice}
        </p>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No scoring schedules yet."
        emptyCta={
          <Can gate={FEATURE_GATES.createInferenceSchedule}>
            <Button variant="outline" size="sm" className="mt-2" onClick={() => { setEditing(null); setFormOpen(true); }}>
              <Plus /> New schedule
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Inference schedules"
          rows={rows}
          columns={columns}
          rowId={(s) => s.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <CalendarClock className="size-8" />
              <p>No scoring schedules</p>
            </div>
          }
        />
      </AsyncBoundary>

      {firesFor && (
        <div className="mt-4">
          <ScheduleFiresPanel schedule={firesFor} onClose={() => setFiresFor(null)} />
        </div>
      )}

      <InferenceScheduleDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editing={editing}
        onSaved={(msg) => {
          setFormOpen(false);
          setEditing(null);
          setNotice(msg);
        }}
      />

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title="Delete schedule"
        description={`Delete "${toDelete?.name ?? toDelete?.id}"? Future fires stop; already-submitted jobs are unaffected.`}
        confirmLabel="Delete"
        destructive
        onConfirm={() => {
          if (toDelete)
            deleteMutation.mutate(toDelete.id, {
              onSuccess: () => setNotice("Schedule deleted."),
              onSettled: () => setToDelete(null),
            });
        }}
      />
    </div>
  );
}

/** Recent jobs this schedule submitted (inference-service GET .../fires). */
function ScheduleFiresPanel({ schedule, onClose }: { schedule: InferenceSchedule; onClose: () => void }) {
  const query = useInferenceScheduleFires(schedule.id);
  const jobs = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">Fires — {schedule.name ?? schedule.id}</CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={jobs.length === 0}
          emptyTitle="This schedule has not fired yet."
          onRetry={() => query.refetch()}
        >
          <ul className="space-y-2 text-sm">
            {jobs.map((j) => (
              <li key={j.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border p-2">
                <Link href={`/ml/inference/${j.id}`} className="font-medium text-primary hover:underline">
                  {j.name ?? j.id}
                </Link>
                <StatusChip status={inferenceStatusUi(j.status)} />
                <span className="text-xs text-muted-foreground">{formatLocal(j.createdAt)}</span>
                {j.rowCount != null && (
                  <span className="text-xs tabular-nums text-muted-foreground">{j.rowCount.toLocaleString()} rows</span>
                )}
              </li>
            ))}
          </ul>
          {query.hasNextPage && (
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              disabled={query.isFetchingNextPage}
              onClick={() => query.fetchNextPage()}
            >
              Load more
            </Button>
          )}
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}

/**
 * Create/edit form. Mirrors the server validation client-side (schedules.py
 * ScheduleService.create): exactly ONE of pinned model version / model+stage,
 * and exactly ONE of cron / intervalSeconds. On edit, only the PATCH-able
 * fields (timing, overlap, selectors, notify) are editable — name and model
 * spec are immutable after creation and shown read-only.
 */
function InferenceScheduleDialog({
  open,
  onOpenChange,
  editing,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  editing: InferenceSchedule | null;
  onSaved: (msg: string) => void;
}) {
  const datasetsQuery = useDatasets();
  const datasets = useMemo(() => datasetsQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [datasetsQuery.data]);
  const modelsQuery = useModels();
  const models = useMemo(() => modelsQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [modelsQuery.data]);

  const createMutation = useCreateInferenceSchedule();
  const updateMutation = useUpdateInferenceSchedule();
  const pending = createMutation.isPending || updateMutation.isPending;

  const [name, setName] = useState("");
  // Model spec XOR: a pinned VERSION urn, or a model + stage selector.
  const [modelMode, setModelMode] = useState<"pinned" | "stage">("pinned");
  const [modelId, setModelId] = useState("");
  const [versionUrn, setVersionUrn] = useState("");
  const [stageSelector, setStageSelector] = useState("production");
  const [datasetUrn, setDatasetUrn] = useState("");
  const [outputName, setOutputName] = useState("");
  const [outputMode, setOutputMode] = useState("append");
  const [timingMode, setTimingMode] = useState<"cron" | "interval">("cron");
  const [cron, setCron] = useState("0 6 * * *");
  const [intervalSeconds, setIntervalSeconds] = useState("3600");
  const [timezone, setTimezone] = useState("UTC");
  const [overlapPolicy, setOverlapPolicy] = useState("skip");
  const [notifyOnFailure, setNotifyOnFailure] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);

  // Version options for the chosen model (pinned mode).
  const modelDetail = useModel(modelMode === "pinned" ? modelId : "");
  const versions = useMemo(() => modelDetail.data?.model?.versions ?? [], [modelDetail.data]);

  useEffect(() => {
    if (!open) return;
    setBanner(null);
    createMutation.reset();
    updateMutation.reset();
    if (editing) {
      setName(editing.name ?? "");
      const sel = (editing.inputSelector ?? {}) as { dataset_urn?: string };
      setDatasetUrn(sel.dataset_urn ?? "");
      const out = (editing.output ?? {}) as { dataset_name?: string; mode?: string };
      setOutputName(out.dataset_name ?? "");
      setOutputMode(out.mode ?? "append");
      setTimingMode(editing.cron ? "cron" : "interval");
      setCron(editing.cron ?? "0 6 * * *");
      setIntervalSeconds(String(editing.intervalSeconds ?? 3600));
      setTimezone(editing.timezone ?? "UTC");
      setOverlapPolicy(editing.overlapPolicy ?? "skip");
      setNotifyOnFailure(editing.notifyOnFailure ?? true);
    } else {
      setName("");
      setModelMode("pinned");
      setModelId("");
      setVersionUrn("");
      setStageSelector("production");
      setDatasetUrn("");
      setOutputName("");
      setOutputMode("append");
      setTimingMode("cron");
      setCron("0 6 * * *");
      setIntervalSeconds("3600");
      setTimezone("UTC");
      setOverlapPolicy("skip");
      setNotifyOnFailure(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only on open/editing change
  }, [open, editing]);

  const submit = () => {
    setBanner(null);
    const interval = Number(intervalSeconds);
    if (timingMode === "interval" && (!Number.isInteger(interval) || interval <= 0)) {
      setBanner("Interval must be a positive number of seconds.");
      return;
    }
    if (timingMode === "cron" && !cron.trim()) {
      setBanner("A cron expression is required in cron mode.");
      return;
    }
    if (!datasetUrn) {
      setBanner("Pick the input dataset to score.");
      return;
    }
    if (editing) {
      updateMutation.mutate(
        {
          id: editing.id,
          input: {
            inputSelector: { dataset_urn: datasetUrn },
            output: { dataset_name: outputName.trim() || undefined, mode: outputMode },
            timezone,
            overlapPolicy,
            notifyOnFailure,
            ...(timingMode === "cron" ? { cron: cron.trim() } : { intervalSeconds: interval }),
          },
        },
        { onSuccess: () => onSaved("Schedule updated.") },
      );
      return;
    }
    if (!name.trim()) {
      setBanner("A schedule name is required.");
      return;
    }
    // XOR mirror of the server's model-spec validation.
    if (modelMode === "pinned" && !versionUrn) {
      setBanner("Pick a model version to pin.");
      return;
    }
    if (modelMode === "stage" && !modelId) {
      setBanner("Pick the model to resolve by stage.");
      return;
    }
    const modelUrn = models.find((m) => m.id === modelId)?.urn;
    createMutation.mutate(
      {
        name: name.trim(),
        // schedules.py _resolve_input_urn reads input_selector.dataset_urn.
        inputSelector: { dataset_urn: datasetUrn },
        output: { dataset_name: outputName.trim() || undefined, mode: outputMode },
        ...(modelMode === "pinned"
          ? { modelVersionUrn: versionUrn }
          : { modelUrn, stageSelector }),
        ...(timingMode === "cron" ? { cron: cron.trim() } : { intervalSeconds: interval }),
        timezone,
        overlapPolicy,
        notifyOnFailure,
      },
      { onSuccess: () => onSaved("Schedule created.") },
    );
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
            {editing ? "Edit schedule" : "New scoring schedule"}
          </Dialog.Title>
          <form
            className="mt-4 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            {editing ? (
              <div className="space-y-1.5 rounded-md border bg-muted/40 p-3 text-sm">
                <p>
                  <span className="text-muted-foreground">Name: </span>
                  <span className="font-medium">{editing.name}</span>
                </p>
                <p className="truncate font-mono text-xs">
                  {editing.modelVersionUrn ?? `${editing.modelUrn ?? ""} @ ${editing.stageSelector ?? ""}`}
                </p>
                <p className="text-xs text-muted-foreground">
                  Name and model cannot be changed after creation (create a new schedule instead).
                </p>
              </div>
            ) : (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="isched-name">Name</Label>
                  <Input id="isched-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nightly claims scoring" />
                </div>

                <div className="space-y-1.5">
                  <Label>Model</Label>
                  <div className="flex items-center gap-3 text-sm" role="radiogroup" aria-label="Model spec">
                    <label className="flex items-center gap-1">
                      <input type="radio" checked={modelMode === "pinned"} onChange={() => setModelMode("pinned")} /> Pinned version
                    </label>
                    <label className="flex items-center gap-1">
                      <input type="radio" checked={modelMode === "stage"} onChange={() => setModelMode("stage")} /> Resolve by stage
                    </label>
                  </div>
                  <select
                    aria-label="Model"
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    value={modelId}
                    onChange={(e) => {
                      setModelId(e.target.value);
                      setVersionUrn("");
                    }}
                  >
                    <option value="">Select a model…</option>
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name ?? m.id}
                      </option>
                    ))}
                  </select>
                  {modelMode === "pinned" && modelId && (
                    <select
                      aria-label="Model version"
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                      value={versionUrn}
                      onChange={(e) => setVersionUrn(e.target.value)}
                      disabled={modelDetail.isLoading}
                    >
                      <option value="">Select a version…</option>
                      {versions.map((v) => (
                        <option key={v.version} value={v.urn}>
                          v{v.version} — {v.stage ?? "none"}
                        </option>
                      ))}
                    </select>
                  )}
                  {modelMode === "stage" && (
                    <select
                      aria-label="Stage selector"
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                      value={stageSelector}
                      onChange={(e) => setStageSelector(e.target.value)}
                    >
                      <option value="production">production</option>
                      <option value="staging">staging</option>
                    </select>
                  )}
                </div>
              </>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="isched-dataset">Input dataset</Label>
              <select
                id="isched-dataset"
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                value={datasetUrn}
                onChange={(e) => setDatasetUrn(e.target.value)}
              >
                <option value="">Select a dataset…</option>
                {datasets.map((d) => (
                  <option key={d.id} value={d.urn}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="isched-output">Output dataset name</Label>
                <Input
                  id="isched-output"
                  value={outputName}
                  onChange={(e) => setOutputName(e.target.value)}
                  placeholder="claims-scores"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="isched-output-mode">Output mode</Label>
                <select
                  id="isched-output-mode"
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  value={outputMode}
                  onChange={(e) => setOutputMode(e.target.value)}
                >
                  <option value="create">create</option>
                  <option value="append">append</option>
                  <option value="replace">replace</option>
                </select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>Timing</Label>
              <div className="flex items-center gap-3 text-sm" role="radiogroup" aria-label="Timing">
                <label className="flex items-center gap-1">
                  <input type="radio" checked={timingMode === "cron"} onChange={() => setTimingMode("cron")} /> Cron
                </label>
                <label className="flex items-center gap-1">
                  <input type="radio" checked={timingMode === "interval"} onChange={() => setTimingMode("interval")} /> Interval
                </label>
              </div>
              {timingMode === "cron" ? (
                <Input
                  aria-label="Cron expression"
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder="0 6 * * *"
                  className="font-mono"
                />
              ) : (
                <Input
                  aria-label="Interval seconds"
                  type="number"
                  min={1}
                  value={intervalSeconds}
                  onChange={(e) => setIntervalSeconds(e.target.value)}
                />
              )}
              <p className="text-xs text-muted-foreground">Exactly one of cron or interval (server-enforced).</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="isched-tz">Timezone</Label>
                <Input id="isched-tz" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="isched-overlap">Overlap policy</Label>
                <select
                  id="isched-overlap"
                  className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                  value={overlapPolicy}
                  onChange={(e) => setOverlapPolicy(e.target.value)}
                >
                  <option value="skip">skip</option>
                  <option value="queue">queue</option>
                  <option value="cancel_running">cancel_running</option>
                </select>
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={notifyOnFailure}
                onChange={(e) => setNotifyOnFailure(e.target.checked)}
              />
              Notify on failure
            </label>

            {banner && <p className="text-xs text-destructive">{banner}</p>}
            {error && (
              <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                {error.message}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? "Saving…" : editing ? "Save" : "Create schedule"}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
