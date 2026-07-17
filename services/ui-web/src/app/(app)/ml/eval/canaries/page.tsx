"use client";
import { useState } from "react";
import { ShieldCheck, Search } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardContent, CardDescription, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useEvalCanary, useCreateEvalCanary, useStopEvalCanary } from "@/lib/graphql/hooks";
import { formatLocal } from "@/lib/utils";

const STATUS_VARIANT: Record<string, "default" | "success" | "destructive" | "secondary"> = {
  collecting: "default",
  ready: "success",
  failed_early: "destructive",
  expired: "secondary",
};

export default function EvalCanariesPage() {
  const [lookupId, setLookupId] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const canary = useEvalCanary(activeId);
  const create = useCreateEvalCanary();
  const stop = useStopEvalCanary();

  return (
    <div>
      <PageHeader title="Canary comparisons" description="Online A/B comparisons between a candidate and baseline agent version (eval-service)." />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Look up a comparison</CardTitle>
            <CardDescription>Enter a comparison id (returned when a canary is started).</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="canary-lookup">Comparison id</Label>
              <Input id="canary-lookup" value={lookupId} onChange={(e) => setLookupId(e.target.value)} placeholder="cc-..." className="w-56" />
            </div>
            <Button variant="outline" disabled={!lookupId.trim()} onClick={() => setActiveId(lookupId.trim())}>
              <Search className="size-4" /> Look up
            </Button>
          </CardContent>
        </Card>

        <Can gate={FEATURE_GATES.manageEvalCanaries}>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Start a new canary</CardTitle>
              <CardDescription>Begins collecting paired candidate/baseline scores.</CardDescription>
            </CardHeader>
            <CardContent>
              <NewCanaryForm
                pending={create.isPending}
                error={create.error}
                onCreate={(input) => create.mutate(input, { onSuccess: (c) => { setActiveId(c.comparisonId); setLookupId(c.comparisonId); } })}
              />
            </CardContent>
          </Card>
        </Can>
      </div>

      {activeId && (
        <div className="mt-4">
          <AsyncBoundary
            isLoading={canary.isLoading}
            isError={canary.isError}
            error={canary.error}
            isEmpty={!canary.data}
            emptyTitle="Comparison not found"
            onRetry={() => canary.refetch()}
          >
            {canary.data && (
              <Card>
                <CardHeader className="flex-row items-center justify-between space-y-0">
                  <div>
                    <CardTitle className="text-sm">{canary.data.comparisonId}</CardTitle>
                    <CardDescription>{canary.data.agentKey}: {canary.data.candidateVersion} vs {canary.data.baselineVersion}</CardDescription>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={STATUS_VARIANT[canary.data.status] ?? "default"}>{canary.data.status}</Badge>
                    {canary.data.status === "collecting" && (
                      <Can gate={FEATURE_GATES.manageEvalCanaries}>
                        <Button size="sm" variant="destructive" disabled={stop.isPending} onClick={() => stop.mutate(canary.data!.comparisonId)}>
                          Stop early
                        </Button>
                      </Can>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <p><span className="text-muted-foreground">samples</span> {canary.data.samples}</p>
                  <p><span className="text-muted-foreground">mode</span> {canary.data.mode}</p>
                  <p><span className="text-muted-foreground">updated</span> {formatLocal(canary.data.updatedAt)}</p>
                  <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(canary.data.report, null, 2)}</pre>
                </CardContent>
              </Card>
            )}
          </AsyncBoundary>
        </div>
      )}

      {!activeId && (
        <div className="mt-8 flex flex-col items-center gap-2 text-muted-foreground">
          <ShieldCheck className="size-8" />
          <p>Look up or start a canary comparison to see its status.</p>
        </div>
      )}
    </div>
  );
}

function NewCanaryForm({
  onCreate,
  pending,
  error,
}: {
  onCreate: (input: { agentKey: string; candidateVersion: string; baselineVersion: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [agentKey, setAgentKey] = useState("");
  const [candidateVersion, setCandidateVersion] = useState("");
  const [baselineVersion, setBaselineVersion] = useState("");

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (agentKey.trim() && candidateVersion.trim() && baselineVersion.trim()) {
          onCreate({ agentKey: agentKey.trim(), candidateVersion: candidateVersion.trim(), baselineVersion: baselineVersion.trim() });
        }
      }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="canary-agent">Agent key</Label>
        <Input id="canary-agent" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} className="h-9 w-40" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="canary-candidate">Candidate version</Label>
        <Input id="canary-candidate" value={candidateVersion} onChange={(e) => setCandidateVersion(e.target.value)} placeholder="v8" className="h-9 w-28" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="canary-baseline">Baseline version</Label>
        <Input id="canary-baseline" value={baselineVersion} onChange={(e) => setBaselineVersion(e.target.value)} placeholder="v7" className="h-9 w-28" />
      </div>
      <Button type="submit" disabled={pending}>Start</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}
