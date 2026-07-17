"use client";
import { useMemo, useState } from "react";
import { Plus, Database, Snowflake } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { EvalSuiteDialog } from "@/components/eval/EvalSuiteDialog";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useEvalDatasets, useCreateEvalDataset, useFreezeEvalDataset } from "@/lib/graphql/hooks";
import type { EvalDataset } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

const STATUS_VARIANT: Record<string, "default" | "success" | "secondary"> = {
  draft: "default",
  frozen: "success",
  archived: "secondary",
};

export default function EvalDatasetsPage() {
  const [agentKey, setAgentKey] = useState("");
  const [creating, setCreating] = useState(false);
  const [suiteOpen, setSuiteOpen] = useState(false);
  const [toFreeze, setToFreeze] = useState<EvalDataset | null>(null);

  const query = useEvalDatasets({ agentKey: agentKey || undefined });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const create = useCreateEvalDataset();
  const freeze = useFreezeEvalDataset();

  const columns: Column<EvalDataset>[] = [
    { id: "key", header: "Dataset key", cell: (d) => <span className="font-mono text-xs">{d.datasetKey}</span> },
    { id: "version", header: "Version", width: 90, cell: (d) => `v${d.version}` },
    { id: "agent", header: "Agent", width: 140, cell: (d) => d.agentKey },
    { id: "status", header: "Status", width: 100, cell: (d) => <Badge variant={STATUS_VARIANT[d.status] ?? "default"}>{d.status}</Badge> },
    { id: "cases", header: "Active cases", width: 110, cell: (d) => d.caseCount },
    { id: "createdAt", header: "Created", width: 170, cell: (d) => formatLocal(d.createdAt) },
    {
      id: "actions", header: "", width: 110,
      cell: (d) =>
        d.status === "draft" ? (
          <Can gate={FEATURE_GATES.manageEvalDatasets}>
            <Button size="sm" variant="outline" onClick={() => setToFreeze(d)}>
              <Snowflake className="size-3" /> Freeze
            </Button>
          </Can>
        ) : null,
    },
  ];

  const newButton = (
    <div className="flex gap-2">
      <Can gate={FEATURE_GATES.createEvalSuite}>
        <Button variant="outline" onClick={() => setSuiteOpen(true)}><Plus /> New suite</Button>
      </Can>
      <Can gate={FEATURE_GATES.manageEvalDatasets}>
        <Button onClick={() => setCreating((v) => !v)}><Plus /> {creating ? "Cancel" : "New dataset"}</Button>
      </Can>
    </div>
  );

  return (
    <div>
      <PageHeader title="Eval datasets" description="Eval dataset versions, copy-on-write per freeze (eval-service)." actions={newButton} />

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-end gap-2 pt-4">
          <div className="flex flex-col gap-1">
            <Label htmlFor="ds-agent-filter">Agent key</Label>
            <Input id="ds-agent-filter" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} placeholder="claims-agent" className="w-56" />
          </div>
        </CardContent>
      </Card>

      {creating && (
        <Card className="mb-4 border-primary/40">
          <CardContent className="pt-4">
            <NewDatasetForm
              pending={create.isPending}
              error={create.error}
              onCreate={(input) => create.mutate(input, { onSuccess: () => setCreating(false) })}
            />
          </CardContent>
        </Card>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No eval datasets yet"
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Eval datasets"
          rows={rows}
          columns={columns}
          rowId={(d) => d.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Database className="size-8" />
              <p>No datasets</p>
            </div>
          }
        />
      </AsyncBoundary>

      <ConfirmDialog
        open={!!toFreeze}
        onOpenChange={(o) => !o && setToFreeze(null)}
        title="Freeze this dataset version?"
        description="Freezing locks the active-case set; further edits copy-on-write to a new draft version. This cannot be undone."
        confirmLabel="Freeze"
        destructive
        onConfirm={() => {
          if (toFreeze) freeze.mutate({ datasetKey: toFreeze.datasetKey, version: toFreeze.version }, { onSuccess: () => setToFreeze(null) });
        }}
      />

      <EvalSuiteDialog open={suiteOpen} onOpenChange={setSuiteOpen} defaultAgentKey={agentKey || undefined} />
    </div>
  );
}

function NewDatasetForm({
  onCreate,
  pending,
  error,
}: {
  onCreate: (input: { datasetKey: string; agentKey: string; description?: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [datasetKey, setDatasetKey] = useState("");
  const [agentKey, setAgentKey] = useState("");
  const [description, setDescription] = useState("");

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (datasetKey.trim() && agentKey.trim()) onCreate({ datasetKey: datasetKey.trim(), agentKey: agentKey.trim(), description: description.trim() || undefined });
      }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="new-ds-key">Dataset key</Label>
        <Input id="new-ds-key" value={datasetKey} onChange={(e) => setDatasetKey(e.target.value)} placeholder="claims-agent/nl2sql" className="h-9 w-56" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="new-ds-agent">Agent key</Label>
        <Input id="new-ds-agent" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} placeholder="claims-agent" className="h-9 w-44" />
      </div>
      <div className="flex flex-1 flex-col gap-1">
        <Label htmlFor="new-ds-desc">Description</Label>
        <Input id="new-ds-desc" value={description} onChange={(e) => setDescription(e.target.value)} className="h-9" />
      </div>
      <Button type="submit" disabled={pending}>Create</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}
