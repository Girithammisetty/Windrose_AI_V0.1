"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Play, CalendarClock } from "lucide-react";
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
  useIngestions,
  useCreateIngestion,
  useConnections,
  useCancelIngestion,
  useRetryIngestion,
  useReingestIngestion,
} from "@/lib/graphql/hooks";
import type { Ingestion } from "@/lib/graphql/types";
import { SchedulesPanel } from "@/components/ingestions/SchedulesPanel";
import { formatLocal, formatNumber } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

// state_machine.py: cancel needs an UNCOMMITTED run; retry needs failed;
// reingest needs a terminal status (completed/failed/cancelled/expired).
const CANCELLABLE = new Set(["created", "awaiting_upload", "queued", "running"]);
const TERMINAL = new Set(["completed", "failed", "cancelled", "expired"]);

export default function DataIngestionsPage() {
  const router = useRouter();
  const query = useIngestions();
  // Task #78: list-wide "ingestion.status"/"ingestion.progress" aren't valid
  // topics (grammar is scheme:identifier; there's no "all ingestions" scheme).
  // Removed rather than left silently 422ing.
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [showForm, setShowForm] = useState(false);
  const [tab, setTab] = useState<"runs" | "schedules">("runs");
  const [banner, setBanner] = useState<string | null>(null);
  const [toCancel, setToCancel] = useState<Ingestion | null>(null);

  const cancelMutation = useCancelIngestion();
  const retryMutation = useRetryIngestion();
  const reingestMutation = useReingestIngestion();
  const lifecyclePending = cancelMutation.isPending || retryMutation.isPending || reingestMutation.isPending;

  const columns: Column<Ingestion>[] = [
    { id: "id", header: "Run", width: 110, cell: (r) => <span className="font-mono text-xs">{r.id.slice(0, 8)}</span> },
    { id: "mode", header: "Mode", width: 130, cell: (r) => r.mode },
    { id: "status", header: "Status", width: 140, cell: (r) => <StatusChip status={r.status.toUpperCase()} live /> },
    { id: "rows", header: "Rows", width: 110, className: "tabular-nums", cell: (r) => formatNumber(r.rowsAppended ?? 0) },
    {
      id: "dataset",
      header: "Target dataset",
      width: "1.5fr",
      cell: (r) => <span className="truncate font-mono text-xs text-muted-foreground">{r.datasetUrn ?? "—"}</span>,
    },
    { id: "created", header: "Started", width: 170, cell: (r) => formatLocal(r.createdAt) },
    {
      id: "actions",
      header: t("ingestions.actions"),
      width: 210,
      cell: (r) => (
        <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          {CANCELLABLE.has(r.status) && (
            <Can gate={FEATURE_GATES.cancelIngestion}>
              <Button variant="ghost" size="sm" disabled={lifecyclePending} onClick={() => setToCancel(r)}>
                {t("ingestions.cancel")}
              </Button>
            </Can>
          )}
          {r.status === "failed" && (
            <Can gate={FEATURE_GATES.retryIngestion}>
              <Button
                variant="outline"
                size="sm"
                disabled={lifecyclePending}
                onClick={() =>
                  retryMutation.mutate(r.id, {
                    onSuccess: () => setBanner(t("ingestions.retried")),
                    onError: (e) => setBanner((e as Error).message),
                  })
                }
              >
                {t("ingestions.retry")}
              </Button>
            </Can>
          )}
          {TERMINAL.has(r.status) && (
            <Can gate={FEATURE_GATES.reingestIngestion}>
              <Button
                variant="ghost"
                size="sm"
                disabled={lifecyclePending}
                onClick={() =>
                  reingestMutation.mutate(r.id, {
                    onSuccess: () => setBanner(t("ingestions.reingested")),
                    onError: (e) => setBanner((e as Error).message),
                  })
                }
              >
                {t("ingestions.reingest")}
              </Button>
            </Can>
          )}
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Ingestions"
        description="Batch ingestion runs that land source data as real dataset versions; status is live."
        actions={
          tab === "runs" ? (
            <Button size="sm" onClick={() => setShowForm((v) => !v)}>
              <Play /> New ingestion
            </Button>
          ) : undefined
        }
      />

      <div className="mb-3 flex items-center gap-1" role="tablist" aria-label="Ingestions view">
        <Button
          role="tab"
          aria-selected={tab === "runs"}
          variant={tab === "runs" ? "default" : "ghost"}
          size="sm"
          onClick={() => setTab("runs")}
        >
          {t("ingestions.tab.runs")}
        </Button>
        <Can gate={FEATURE_GATES.viewIngestionSchedules}>
          <Button
            role="tab"
            aria-selected={tab === "schedules"}
            variant={tab === "schedules" ? "default" : "ghost"}
            size="sm"
            onClick={() => setTab("schedules")}
          >
            <CalendarClock /> {t("ingestions.tab.schedules")}
          </Button>
        </Can>
      </div>

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="notice-banner">
          {banner}
        </div>
      )}

      {tab === "schedules" ? (
        <SchedulesPanel onNotice={setBanner} />
      ) : (
        <>
          {showForm && <NewIngestionForm onDone={() => setShowForm(false)} />}

          <AsyncBoundary
            isLoading={query.isLoading}
            isError={query.isError}
            error={query.error}
            isEmpty={rows.length === 0}
            emptyTitle="No ingestion runs yet"
            emptyCta={
              <Button variant="outline" size="sm" onClick={() => setShowForm(true)}>
                Start an ingestion
              </Button>
            }
            onRetry={() => query.refetch()}
          >
            <DataTable
              ariaLabel="Ingestion runs"
              rows={rows}
              columns={columns}
              rowId={(r) => r.id}
              hasMore={query.hasNextPage}
              isFetchingMore={query.isFetchingNextPage}
              onLoadMore={() => query.fetchNextPage()}
              onRowActivate={(r) => r.datasetUrn && router.push(`/data/datasets/${r.datasetUrn.split("/").pop()}`)}
            />
          </AsyncBoundary>
        </>
      )}

      <ConfirmDialog
        open={!!toCancel}
        onOpenChange={(o) => !o && setToCancel(null)}
        title={t("ingestions.cancel")}
        description={t("ingestions.cancelConfirm")}
        confirmLabel={t("ingestions.cancel")}
        destructive
        onConfirm={() => {
          if (toCancel)
            cancelMutation.mutate(toCancel.id, {
              onSuccess: () => setBanner(t("ingestions.cancelled")),
              onError: (e) => setBanner((e as Error).message),
              onSettled: () => setToCancel(null),
            });
        }}
      />
    </div>
  );
}

/** Query-mode ingestion: pull rows from a saved connection into a NEW dataset. */
function NewIngestionForm({ onDone }: { onDone: () => void }) {
  const connections = useConnections();
  const create = useCreateIngestion();
  const conns = useMemo(() => connections.data?.pages.flatMap((p) => p.nodes) ?? [], [connections.data]);
  const [connectionId, setConnectionId] = useState("");
  const [statement, setStatement] = useState("SELECT 1 AS n");
  const [datasetName, setDatasetName] = useState("");

  const submit = () =>
    create.mutate(
      { mode: "query", connectionId, statement, newDatasetName: datasetName },
      { onSuccess: onDone },
    );

  return (
    <Card className="mb-4">
      <CardHeader>
        <CardTitle className="text-sm">New query ingestion</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <Label htmlFor="conn">Source connection</Label>
          <select
            id="conn"
            className="mt-1 h-9 w-full rounded-md border bg-transparent px-3 text-sm"
            value={connectionId}
            onChange={(e) => setConnectionId(e.target.value)}
          >
            <option value="">Select a connection…</option>
            {conns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.connectorType})
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="stmt">SQL statement</Label>
          <Textarea
            id="stmt"
            rows={3}
            className="mt-1 font-mono text-xs"
            value={statement}
            onChange={(e) => setStatement(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="dsname">New dataset name</Label>
          <Input id="dsname" className="mt-1" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
        </div>
        {create.isError && <p className="text-sm text-destructive">{(create.error as Error).message}</p>}
        <div className="flex gap-2">
          <Button size="sm" onClick={submit} disabled={!connectionId || !datasetName || create.isPending}>
            {create.isPending ? "Starting…" : "Start ingestion"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
