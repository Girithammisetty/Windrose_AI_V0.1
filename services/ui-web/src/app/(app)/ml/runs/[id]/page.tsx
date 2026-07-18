"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { StatusChip } from "@/components/primitives/StatusChip";
import { UrnLink } from "@/components/primitives/UrnLink";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, Input, Label, Textarea } from "@/components/ui/primitives";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useRun,
  // Tier 4b: ml ops — register/notes/artifacts/metric history.
  useRegisterRunAsModel,
  useRunNote,
  useUpsertRunNote,
  useDeleteRunNote,
  useRunArtifacts,
  useRunArtifactUrl,
  useRunMetricHistory,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { useHubTopics } from "@/lib/realtime/useHubTopics";
import { formatBytes, formatLocal } from "@/lib/utils";
import type { MetricHistoryRow } from "@/lib/graphql/types";

const TABS = ["metrics", "params", "model", "notes", "artifacts", "history"] as const;

function toEntries(value: unknown): [string, unknown][] {
  if (!value || typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>);
}

/** Numeric metrics rendered as plain proportional bars (no chart library). */
function MetricsGrid({ metrics }: { metrics: unknown }) {
  const entries = toEntries(metrics);
  if (entries.length === 0) return <p className="text-sm text-muted-foreground">No metrics recorded.</p>;
  const numeric = entries.filter(([, v]) => typeof v === "number") as [string, number][];
  const max = Math.max(1, ...numeric.map(([, v]) => Math.abs(v)));
  return (
    <dl className="space-y-2">
      {entries.map(([k, v]) => {
        const num = typeof v === "number" ? v : null;
        return (
          <div key={k}>
            <div className="flex items-center justify-between text-sm">
              <dt className="font-mono text-xs text-muted-foreground">{k}</dt>
              <dd className="font-medium tabular-nums">{num != null ? num : String(v)}</dd>
            </div>
            {num != null && (
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full bg-primary" style={{ width: `${Math.min(100, (Math.abs(num) / max) * 100)}%` }} />
              </div>
            )}
          </div>
        );
      })}
    </dl>
  );
}

function KeyValueGrid({ data }: { data: unknown }) {
  const entries = toEntries(data);
  if (entries.length === 0) return <p className="text-sm text-muted-foreground">No parameters recorded.</p>;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
      {entries.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="truncate font-mono text-xs text-muted-foreground">{k}</dt>
          <dd className="truncate font-medium">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function RunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useRun(id);
  const run = query.data?.run;
  // Task #78: "run.status" wasn't a valid topic — real subscription is
  // run-status:<run-urn> (experiment-service's run.* events carry
  // resource_urn = the run's own URN; see routing.go's "experiment_run" rule).
  useHubTopics(run?.urn ? [`run-status:${run.urn}`] : []);
  const [registerOpen, setRegisterOpen] = useState(false);

  return (
    <div>
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && !run}
        emptyTitle="Run not found"
        onRetry={() => query.refetch()}
      >
        {run && (
          <>
            <PageHeader
              title={run.name ?? "Training run"}
              actions={
                <div className="flex items-center gap-2">
                  {/* Register needs the owning experiment id; the run detail
                     payload always carries it. */}
                  {run.experimentId && (
                    <Can gate={FEATURE_GATES.registerModel}>
                      <Button size="sm" onClick={() => setRegisterOpen(true)}>
                        Register as model
                      </Button>
                    </Can>
                  )}
                  <StatusChip status={run.status} live />
                </div>
              }
            />

            <Tabs.Root defaultValue="metrics">
              <Tabs.List className="mb-3 flex gap-1 border-b" aria-label="Run sections">
                {TABS.map((v) => (
                  <Tabs.Trigger
                    key={v}
                    value={v}
                    className="border-b-2 border-transparent px-3 py-2 text-sm font-medium capitalize text-muted-foreground data-[state=active]:border-primary data-[state=active]:text-foreground"
                  >
                    {v}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>

              <Tabs.Content value="metrics">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">Metrics</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <MetricsGrid metrics={run.metrics} />
                  </CardContent>
                </Card>
              </Tabs.Content>

              <Tabs.Content value="params">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">Parameters</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <KeyValueGrid data={run.params} />
                  </CardContent>
                </Card>
              </Tabs.Content>

              <Tabs.Content value="model">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">Registered model</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    {run.model ? (
                      <>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">Model</span>
                          <UrnLink urn={run.model.urn} label={run.model.name ?? undefined} />
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">Stage</span>
                          <span className="font-medium">{run.model.stage ?? "—"}</span>
                        </div>
                      </>
                    ) : (
                      <p className="text-muted-foreground">This run has no registered model.</p>
                    )}
                  </CardContent>
                </Card>
              </Tabs.Content>

              <Tabs.Content value="notes">
                <RunNotesTab runId={id} />
              </Tabs.Content>

              <Tabs.Content value="artifacts">
                <RunArtifactsTab runId={id} />
              </Tabs.Content>

              <Tabs.Content value="history">
                <RunMetricHistoryTab runId={id} />
              </Tabs.Content>
            </Tabs.Root>

            {run.experimentId && (
              <RegisterModelDialog
                open={registerOpen}
                onOpenChange={setRegisterOpen}
                experimentId={run.experimentId}
                runId={id}
              />
            )}
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}

/**
 * Register this run as a model version (experiment-service register, 201).
 * A not-finished run answers RunNotFinished — surfaced verbatim below the form.
 */
function RegisterModelDialog({
  open,
  onOpenChange,
  experimentId,
  runId,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  experimentId: string;
  runId: string;
}) {
  const register = useRegisterRunAsModel();
  const [modelName, setModelName] = useState("");
  const [description, setDescription] = useState("");
  const [flavor, setFlavor] = useState("");
  const error = register.error instanceof GraphQLRequestError ? register.error : (register.error as Error | null);
  const done = register.isSuccess ? register.data : null;

  useEffect(() => {
    if (!open) return;
    register.reset();
    setModelName("");
    setDescription("");
    setFlavor("");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the dialog opens
  }, [open]);

  const submit = () => {
    if (!modelName.trim()) return;
    register.mutate({
      experimentId,
      runId,
      input: {
        modelName: modelName.trim(),
        description: description.trim() || undefined,
        flavor: flavor.trim() || undefined,
      },
    });
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">Register as model</Dialog.Title>
          {done ? (
            <div className="mt-4 space-y-2 text-sm" data-testid="register-result">
              <p className="font-medium text-[hsl(var(--success))]">
                Registered as v{done.version}
                {done.modelCreated ? " (new model created)" : ""} — stage {done.stage ?? "none"}.
              </p>
              <Link href={`/ml/models/${done.modelId}`} className="text-primary hover:underline">
                Open model {done.modelId}
              </Link>
              <div className="flex justify-end pt-2">
                <Button onClick={() => onOpenChange(false)}>Done</Button>
              </div>
            </div>
          ) : (
            <form
              className="mt-4 space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
            >
              <div className="space-y-1.5">
                <Label htmlFor="register-model-name">Model name</Label>
                <Input
                  id="register-model-name"
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                  placeholder="claims-severity"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="register-description">Description (optional)</Label>
                <Input id="register-description" value={description} onChange={(e) => setDescription(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="register-flavor">Flavor (optional)</Label>
                <Input
                  id="register-flavor"
                  value={flavor}
                  onChange={(e) => setFlavor(e.target.value)}
                  placeholder="mlflow.sklearn (default)"
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
                <Button type="submit" disabled={register.isPending || !modelName.trim()}>
                  {register.isPending ? "Registering…" : "Register"}
                </Button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** The run's free-text note (experiment-service /runs/{id}/note; GET 404 = no
 * note yet → empty editor). Writes gated on experiment.run.update. */
function RunNotesTab({ runId }: { runId: string }) {
  const noteQuery = useRunNote(runId);
  const upsert = useUpsertRunNote();
  const remove = useDeleteRunNote();
  const [draft, setDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const note = noteQuery.data ?? null;

  // Prefill once the real note loads (and on refetch after save/delete).
  useEffect(() => {
    setDraft(note?.description ?? "");
  }, [note?.description]);

  const error = (upsert.error ?? remove.error) as Error | null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Notes</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <AsyncBoundary
          isLoading={noteQuery.isLoading}
          isError={noteQuery.isError}
          error={noteQuery.error}
          isEmpty={false}
          onRetry={() => noteQuery.refetch()}
        >
          <Textarea
            aria-label="Run note"
            rows={5}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="What made this run notable?"
          />
          {notice && (
            <p role="status" className="text-xs text-muted-foreground">
              {notice}
            </p>
          )}
          {error && (
            <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
              {error.message}
            </p>
          )}
          <Can gate={FEATURE_GATES.updateRun}>
            <div className="flex justify-end gap-2">
              {note && (
                <Button variant="outline" size="sm" onClick={() => setConfirmDelete(true)} disabled={remove.isPending}>
                  Delete note
                </Button>
              )}
              <Button
                size="sm"
                disabled={upsert.isPending || !draft.trim()}
                onClick={() =>
                  upsert.mutate(
                    { runId, description: draft.trim() },
                    { onSuccess: () => setNotice("Note saved.") },
                  )
                }
              >
                {upsert.isPending ? "Saving…" : "Save note"}
              </Button>
            </div>
          </Can>
        </AsyncBoundary>
      </CardContent>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete note"
        description="Remove this run's note? This cannot be undone."
        confirmLabel="Delete note"
        destructive
        onConfirm={() => {
          remove.mutate(runId, {
            onSuccess: () => {
              setDraft("");
              setNotice("Note deleted.");
            },
            onSettled: () => setConfirmDelete(false),
          });
        }}
      />
    </Card>
  );
}

/** Artifact index + per-row on-demand REAL signed url (never pre-fetched —
 * links are short-lived, so each is minted per click). */
function RunArtifactsTab({ runId }: { runId: string }) {
  const query = useRunArtifacts(runId);
  const urlMutation = useRunArtifactUrl();
  const [links, setLinks] = useState<Record<string, string>>({});
  const [pendingPath, setPendingPath] = useState<string | null>(null);
  const artifacts = query.data ?? [];
  const error = urlMutation.error as Error | null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Artifacts</CardTitle>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={artifacts.length === 0}
          emptyTitle="This run has no artifacts."
          onRetry={() => query.refetch()}
        >
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-2 pr-4 font-medium">Path</th>
                <th className="py-2 pr-4 font-medium">Size</th>
                <th className="py-2 pr-4 font-medium">Type</th>
                <th className="py-2 text-right font-medium">Download</th>
              </tr>
            </thead>
            <tbody>
              {artifacts.map((a) => (
                <tr key={a.path} className="border-b last:border-0">
                  <td className="py-2 pr-4 font-mono text-xs">{a.path}</td>
                  <td className="py-2 pr-4 tabular-nums">{a.sizeBytes != null ? formatBytes(a.sizeBytes) : "—"}</td>
                  <td className="py-2 pr-4 text-xs text-muted-foreground">{a.contentType ?? "—"}</td>
                  <td className="py-2 text-right">
                    {links[a.path] ? (
                      <a
                        href={links[a.path]}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary hover:underline"
                      >
                        Open signed link
                      </a>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={urlMutation.isPending && pendingPath === a.path}
                        onClick={() => {
                          setPendingPath(a.path);
                          urlMutation.mutate(
                            { runId, path: a.path },
                            {
                              onSuccess: (url) => setLinks((prev) => ({ ...prev, [a.path]: url })),
                              onSettled: () => setPendingPath(null),
                            },
                          );
                        }}
                      >
                        {urlMutation.isPending && pendingPath === a.path ? "Fetching…" : "Get link"}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {error && (
            <p role="alert" className="mt-2 text-xs text-destructive" data-testid="mutation-error">
              {error.message}
            </p>
          )}
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}

/** Raw logged metric points ({key, step, value, logged_at}) — verbatim rows
 * from experiment-service metric-history. */
function RunMetricHistoryTab({ runId }: { runId: string }) {
  const query = useRunMetricHistory(runId);
  const rows = (Array.isArray(query.data) ? query.data : []) as MetricHistoryRow[];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Metric history</CardTitle>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={rows.length === 0}
          emptyTitle="No logged metric points for this run."
          onRetry={() => query.refetch()}
        >
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-2 pr-4 font-medium">Metric</th>
                <th className="py-2 pr-4 font-medium">Step</th>
                <th className="py-2 pr-4 font-medium">Value</th>
                <th className="py-2 font-medium">Logged at</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.key}-${r.step}-${i}`} className="border-b last:border-0">
                  <td className="py-1.5 pr-4 font-mono text-xs">{r.key}</td>
                  <td className="py-1.5 pr-4 tabular-nums">{r.step ?? "—"}</td>
                  <td className="py-1.5 pr-4 font-medium tabular-nums">{r.value ?? "—"}</td>
                  <td className="py-1.5 text-xs text-muted-foreground">{formatLocal(r.logged_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}
