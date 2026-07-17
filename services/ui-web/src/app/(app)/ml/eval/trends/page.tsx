"use client";
import { useMemo, useState } from "react";
import { GitCompareArrows } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Card, CardContent, CardHeader, CardTitle, CardDescription, Badge, Input, Label } from "@/components/ui/primitives";
import { useEvalTrends, useEvalSlos } from "@/lib/graphql/hooks";
import type { EvalTrendPoint } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

/**
 * The model-version scorecard: every scoring run's aggregate score, grouped by
 * agent version, for one agent + (optional) scorer. This is the raw comparison
 * data eval-service exposes via GET /trends — there is no separate "scorecard"
 * endpoint, so the comparison view is this series read as a table + per-version summary.
 */
export default function EvalTrendsPage() {
  const [agentKey, setAgentKey] = useState("");
  const [scorer, setScorer] = useState("");
  const [window, setWindow] = useState("30d");
  const [submitted, setSubmitted] = useState<{ agentKey: string; scorer: string; window: string } | null>(null);

  const query = useEvalTrends(submitted?.agentKey ?? "", submitted?.scorer || undefined, submitted?.window);
  const points = useMemo(() => query.data ?? [], [query.data]);
  const slos = useEvalSlos(submitted?.agentKey ?? "", submitted?.window === "7d" ? "7d" : "24h");

  const byVersion = useMemo(() => {
    const groups = new Map<string, EvalTrendPoint[]>();
    for (const p of points) {
      const key = p.agentVersion ?? "(unversioned)";
      groups.set(key, [...(groups.get(key) ?? []), p]);
    }
    return Array.from(groups.entries()).map(([version, pts]) => {
      const scorers = new Set(pts.map((p) => p.scorer));
      const meanOfMeans = pts.reduce((s, p) => s + (p.mean ?? 0), 0) / (pts.length || 1);
      return { version, count: pts.length, scorers: scorers.size, meanOfMeans, latest: pts[pts.length - 1]?.at };
    });
  }, [points]);

  const columns: Column<EvalTrendPoint>[] = [
    { id: "run", header: "Run", cell: (p) => <span className="font-mono text-xs">{p.runId}</span> },
    { id: "version", header: "Agent version", width: 140, cell: (p) => p.agentVersion ?? "—" },
    { id: "scorer", header: "Scorer", width: 160, cell: (p) => p.scorer },
    { id: "mean", header: "Mean", width: 90, cell: (p) => (p.mean != null ? p.mean.toFixed(3) : "—") },
    { id: "passRate", header: "Pass rate", width: 100, cell: (p) => (p.passRate != null ? `${(p.passRate * 100).toFixed(1)}%` : "—") },
    { id: "at", header: "At", width: 170, cell: (p) => formatLocal(p.at) },
  ];

  return (
    <div>
      <PageHeader title="Trends / model-version scorecard" description="Score history across agent versions and scorers (eval-service GET /trends)." />

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-end gap-2 pt-4">
          <div className="flex flex-col gap-1">
            <Label htmlFor="trend-agent">Agent key</Label>
            <Input id="trend-agent" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} placeholder="claims-agent" className="w-56" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="trend-scorer">Scorer (optional)</Label>
            <Input id="trend-scorer" value={scorer} onChange={(e) => setScorer(e.target.value)} placeholder="exact_match" className="w-48" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="trend-window">Window</Label>
            <select id="trend-window" value={window} onChange={(e) => setWindow(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
              <option value="7d">7d</option>
              <option value="30d">30d</option>
              <option value="90d">90d</option>
            </select>
          </div>
          <button
            type="button"
            className="h-9 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
            disabled={!agentKey.trim()}
            onClick={() => setSubmitted({ agentKey: agentKey.trim(), scorer: scorer.trim(), window })}
          >
            Load
          </button>
        </CardContent>
      </Card>

      {submitted && (
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={points.length === 0}
          emptyTitle="No completed runs in this window"
          onRetry={() => query.refetch()}
        >
          <div className="space-y-4">
            {byVersion.length > 1 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Version comparison</CardTitle>
                  <CardDescription>Mean-of-means across scorers, per agent version — a quick scorecard read.</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-3">
                  {byVersion.map((v) => (
                    <Badge key={v.version} variant="secondary" className="px-3 py-1.5 text-sm">
                      {v.version}: {v.meanOfMeans.toFixed(3)} ({v.count} points across {v.scorers} scorer{v.scorers === 1 ? "" : "s"})
                    </Badge>
                  ))}
                </CardContent>
              </Card>
            )}

            <DataTable
              ariaLabel="Trend points"
              rows={points}
              columns={columns}
              rowId={(p) => `${p.runId}-${p.scorer}`}
              emptyState={
                <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
                  <GitCompareArrows className="size-8" />
                  <p>No trend points</p>
                </div>
              }
            />

            {slos.data && slos.data.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">SLO rollups</CardTitle>
                  <CardDescription>Operational health (eval-service GET /slos), not score quality.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {slos.data.map((row, i) => (
                    <div key={i} className="rounded-md border p-2 text-xs">
                      <p className="font-medium">{row.window} · {row.agentVersion ?? "all versions"} {row.tenantId == null && <Badge variant="secondary" className="ml-1">platform</Badge>}</p>
                      <pre className="mt-1 overflow-auto">{JSON.stringify(row.metrics, null, 2)}</pre>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
          </div>
        </AsyncBoundary>
      )}
    </div>
  );
}
