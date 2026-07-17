"use client";
import { useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Plus, FlaskConical } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useEvalRuns, useCreateEvalRun } from "@/lib/graphql/hooks";
import type { EvalRun } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

const STATUS_VARIANT: Record<string, "default" | "success" | "warning" | "destructive"> = {
  completed: "success",
  running: "default",
  scoring: "default",
  queued: "warning",
  failed: "destructive",
};

export default function EvalRunsPage() {
  const router = useRouter();
  const params = useSearchParams();
  const initialAgentKey = params.get("agentKey") ?? "";
  const [agentKey, setAgentKey] = useState(initialAgentKey);
  const [trigger, setTrigger] = useState("");
  const [creating, setCreating] = useState(false);

  const query = useEvalRuns({ agentKey: agentKey || undefined, trigger: trigger || undefined });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const create = useCreateEvalRun();

  const columns: Column<EvalRun>[] = [
    { id: "id", header: "Run", cell: (r) => <span className="font-mono text-xs">{r.id}</span> },
    { id: "trigger", header: "Trigger", width: 110, cell: (r) => r.trigger },
    {
      id: "status", header: "Status", width: 110,
      cell: (r) => <Badge variant={STATUS_VARIANT[r.status] ?? "default"}>{r.status}</Badge>,
    },
    { id: "cost", header: "Cost", width: 90, cell: (r) => `$${r.costUsd.toFixed(4)}` },
    { id: "createdAt", header: "Created", width: 170, cell: (r) => formatLocal(r.createdAt) },
  ];

  const newButton = (
    <Can gate={FEATURE_GATES.createEvalRun}>
      <Button onClick={() => setCreating((v) => !v)}>
        <Plus /> {creating ? "Cancel" : "New run"}
      </Button>
    </Can>
  );

  return (
    <div>
      <PageHeader title="Eval runs" description="Real scoring runs against a suite (eval-service)." actions={newButton} />

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-end gap-2 pt-4">
          <div className="flex flex-col gap-1">
            <Label htmlFor="filter-agent">Agent key</Label>
            <Input id="filter-agent" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} placeholder="claims-agent" className="w-56" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="filter-trigger">Trigger</Label>
            <select
              id="filter-trigger"
              value={trigger}
              onChange={(e) => setTrigger(e.target.value)}
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            >
              <option value="">any</option>
              <option value="manual">manual</option>
              <option value="ci">ci</option>
              <option value="publish_gate">publish_gate</option>
              <option value="scheduled_online">scheduled_online</option>
              <option value="canary">canary</option>
            </select>
          </div>
        </CardContent>
      </Card>

      {creating && (
        <NewRunForm
          defaultAgentKey={agentKey}
          pending={create.isPending}
          error={create.error}
          onCreate={(input) =>
            create.mutate(input, { onSuccess: (r) => router.push(`/ml/eval/runs/${r.id}`) })
          }
        />
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No runs match this filter"
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Eval runs"
          rows={rows}
          columns={columns}
          rowId={(r) => r.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          onRowActivate={(r) => router.push(`/ml/eval/runs/${r.id}`)}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <FlaskConical className="size-8" />
              <p>No runs</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}

function NewRunForm({
  defaultAgentKey,
  onCreate,
  pending,
  error,
}: {
  defaultAgentKey: string;
  onCreate: (input: { agentKey: string; suiteId: string; suiteVersion?: number; candidate: { content_digest: string; agent_version?: string } }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [agentKey, setAgentKey] = useState(defaultAgentKey);
  const [suiteId, setSuiteId] = useState("");
  const [suiteVersion, setSuiteVersion] = useState("");
  const [agentVersion, setAgentVersion] = useState("");
  const [contentDigest, setContentDigest] = useState("");

  const submit = () => {
    if (!agentKey.trim() || !suiteId.trim() || !contentDigest.trim()) return;
    onCreate({
      agentKey: agentKey.trim(),
      suiteId: suiteId.trim(),
      suiteVersion: suiteVersion.trim() ? Number(suiteVersion) : undefined,
      candidate: { content_digest: contentDigest.trim(), agent_version: agentVersion.trim() || undefined },
    });
  };

  return (
    <Card className="mb-4 border-primary/40">
      <CardContent className="pt-4">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => { e.preventDefault(); submit(); }}
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor="run-agent">Agent key</Label>
            <Input id="run-agent" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} className="h-9 w-44" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="run-suite">Suite id</Label>
            <Input id="run-suite" value={suiteId} onChange={(e) => setSuiteId(e.target.value)} placeholder="nl2sql" className="h-9 w-40" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="run-suite-version">Suite version</Label>
            <Input id="run-suite-version" type="number" min="1" value={suiteVersion} onChange={(e) => setSuiteVersion(e.target.value)} placeholder="latest" className="h-9 w-28" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="run-digest">Candidate content digest</Label>
            <Input id="run-digest" value={contentDigest} onChange={(e) => setContentDigest(e.target.value)} placeholder="sha256:..." className="h-9 w-56" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="run-agent-version">Agent version (optional)</Label>
            <Input id="run-agent-version" value={agentVersion} onChange={(e) => setAgentVersion(e.target.value)} placeholder="v7" className="h-9 w-28" />
          </div>
          <Button type="submit" disabled={pending}>Run</Button>
          {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
        </form>
        <p className="mt-2 text-xs text-muted-foreground">
          This submits a REAL scoring run — eval-service executes the suite against the candidate synchronously
          and returns the completed (or failed) result.
        </p>
      </CardContent>
    </Card>
  );
}
