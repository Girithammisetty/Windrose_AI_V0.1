"use client";
import { use } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { AiLabel } from "@/components/primitives/AiLabel";
import { TraceVisualizer } from "@/components/copilot/TraceVisualizer";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { useAgentRun } from "@/lib/graphql/hooks";
import { agentLabel } from "@/lib/labels";
import { formatUsd } from "@/lib/utils";

export default function AgentRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useAgentRun(id);
  const run = query.data?.agentRun;

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
              title={run.agentKey ? agentLabel(run.agentKey) : "Assistant run"}
              actions={
                <div className="flex items-center gap-2">
                  <AiLabel />
                  <StatusChip status={run.status} />
                </div>
              }
            />
            <div className="mb-4 grid gap-3 sm:grid-cols-3">
              <Stat label="Cost" value={formatUsd(run.costUsd)} />
              <Stat label="Input tokens" value={String(run.tokenUsage?.inputTokens ?? "—")} />
              <Stat label="Output tokens" value={String(run.tokenUsage?.outputTokens ?? "—")} />
            </div>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Trace</CardTitle>
              </CardHeader>
              <CardContent>
                <TraceVisualizer trace={run.trace} />
              </CardContent>
            </Card>
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="pt-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-lg font-semibold">{value}</p>
      </CardContent>
    </Card>
  );
}
