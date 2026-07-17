"use client";
import { use, useState } from "react";
import { useRouter } from "next/navigation";
import { XCircle, CheckCircle2, AlertTriangle } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { EvalSuiteDialog } from "@/components/eval/EvalSuiteDialog";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useEvalRun, useCancelEvalRun } from "@/lib/graphql/hooks";
import type { EvalCaseResult } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

export default function EvalRunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const query = useEvalRun(id);
  const cancel = useCancelEvalRun();
  const [editingSuite, setEditingSuite] = useState(false);
  const run = query.data;

  const columns: Column<EvalCaseResult>[] = [
    { id: "case", header: "Case", cell: (r) => <span className="font-mono text-xs">{r.caseId}</span> },
    { id: "scorer", header: "Scorer", cell: (r) => `${r.scorerKey}@${r.scorerVersion}` },
    { id: "score", header: "Score", width: 90, cell: (r) => r.score.toFixed(3) },
    {
      id: "passed", header: "Passed", width: 90,
      cell: (r) => (r.passed ? <CheckCircle2 className="size-4 text-[hsl(var(--success))]" /> : <XCircle className="size-4 text-destructive" />),
    },
    { id: "latency", header: "Latency", width: 90, cell: (r) => (r.latencyMs != null ? `${r.latencyMs}ms` : "—") },
    { id: "cost", header: "Cost", width: 90, cell: (r) => `$${r.costUsd.toFixed(4)}` },
  ];

  return (
    <div>
      <PageHeader
        title={`Run ${id}`}
        description="Case results, suite pins, and promotion-gate status for one scoring run."
        actions={
          run && (run.status === "running" || run.status === "queued" || run.status === "scoring") ? (
            <Can gate={FEATURE_GATES.cancelEvalRun}>
              <Button variant="destructive" disabled={cancel.isPending} onClick={() => cancel.mutate(id)}>
                Cancel run
              </Button>
            </Can>
          ) : undefined
        }
      />

      <AsyncBoundary isLoading={query.isLoading} isError={query.isError} error={query.error} onRetry={() => query.refetch()}>
        {run && (
          <div className="space-y-4">
            <div className="grid gap-4 lg:grid-cols-3">
              <Card>
                <CardHeader><CardTitle className="text-sm">Candidate</CardTitle></CardHeader>
                <CardContent className="space-y-1 text-sm">
                  <p><span className="text-muted-foreground">agent</span> {run.agentKey}</p>
                  <p><span className="text-muted-foreground">trigger</span> {run.trigger}</p>
                  <p><span className="text-muted-foreground">status</span> <Badge>{run.status}</Badge></p>
                  <p><span className="text-muted-foreground">cost</span> ${run.costUsd.toFixed(4)} / cap ${run.costCapUsd.toFixed(2)}</p>
                  <p><span className="text-muted-foreground">started by</span> {run.startedBy ?? "—"}</p>
                  <p><span className="text-muted-foreground">created</span> {formatLocal(run.createdAt)}</p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex-row items-center justify-between space-y-0">
                  <CardTitle className="text-sm">Suite</CardTitle>
                  {run.suite && (
                    <Can gate={FEATURE_GATES.createEvalSuite}>
                      <Button variant="ghost" size="sm" onClick={() => setEditingSuite(true)}>Edit</Button>
                    </Can>
                  )}
                </CardHeader>
                <CardContent className="space-y-1 text-sm">
                  {run.suite ? (
                    <>
                      <p><span className="text-muted-foreground">suite id</span> {run.suite.suiteId} v{run.suite.version}</p>
                      <p><span className="text-muted-foreground">gate rule</span> <code className="text-xs">{run.suite.gateRule}</code></p>
                      <p><span className="text-muted-foreground">min cases</span> {run.suite.minCases}</p>
                    </>
                  ) : (
                    <p className="text-muted-foreground">Suite pin not resolvable (deleted or no read access).</p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader><CardTitle className="text-sm">Gate</CardTitle></CardHeader>
                <CardContent className="space-y-1 text-sm">
                  {run.gate ? (
                    <>
                      <p className="flex items-center gap-2">
                        {run.gate.gatePassed ? (
                          <><CheckCircle2 className="size-4 text-[hsl(var(--success))]" /> Passed</>
                        ) : (
                          <><XCircle className="size-4 text-destructive" /> Failed</>
                        )}
                      </p>
                      <p><span className="text-muted-foreground">gate run</span> <span className="font-mono text-xs">{run.gate.gateRunId}</span></p>
                    </>
                  ) : (
                    <p className="flex items-center gap-2 text-muted-foreground">
                      <AlertTriangle className="size-4" /> No gate evaluated for this candidate yet.
                    </p>
                  )}
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Aggregate totals</CardTitle>
                <CardDescription>Per-scorer mean / pass-rate over this run&apos;s cases.</CardDescription>
              </CardHeader>
              <CardContent>
                <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(run.totals, null, 2)}</pre>
              </CardContent>
            </Card>

            <div>
              <h2 className="mb-2 text-sm font-medium">Case results</h2>
              <DataTable
                ariaLabel="Case results"
                rows={run.cases ?? []}
                columns={columns}
                rowId={(r) => r.id}
                onRowActivate={() => router.push(`/ml/eval/cases`)}
              />
            </div>
          </div>
        )}
      </AsyncBoundary>

      {run?.suite && (
        <EvalSuiteDialog open={editingSuite} onOpenChange={setEditingSuite} editSuite={run.suite} />
      )}
    </div>
  );
}
