"use client";
import { use } from "react";
import { useDashboard } from "@/lib/graphql/hooks";
import { ChartView } from "@/components/charts/ChartView";
import { useEmbedFrame } from "@/lib/embed/useEmbedFrame";

/**
 * Headless embedded dashboard (embedded-UI increment 1). Rendered under the
 * root layout with NO AppShell (no sidebar/topbar) so it drops cleanly into a
 * tenant's <iframe>. Auth comes from the short-lived embed token the middleware
 * moved from `?t=` into the `wr_embed` cookie; the standard /api/graphql data
 * path then works unchanged. Read-only: no cross-filter, no chrome, no nav.
 */
export default function EmbeddedDashboardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  useEmbedFrame();
  const query = useDashboard(id);
  const dash = query.data?.dashboard;

  return (
    <main id="main" className="min-h-screen bg-background p-4">
      {query.isLoading ? (
        <p className="p-8 text-center text-sm text-muted-foreground">Loading…</p>
      ) : query.isError || !dash ? (
        <p className="p-8 text-center text-sm text-destructive">
          This dashboard is unavailable.
        </p>
      ) : (
        <div className="space-y-4">
          <h1 className="text-lg font-semibold tracking-tight">{dash.title}</h1>
          <div className="grid gap-4 md:grid-cols-2">
            {dash.charts.map((chart) => (
              <div key={chart.id} className="rounded-lg border p-4">
                <h2 className="mb-2 text-sm font-medium">{chart.name}</h2>
                <ChartView
                  chartType={chart.chartType}
                  columns={chart.data?.columns}
                  rows={chart.data?.rows}
                  artifact={chart.data?.artifact}
                  title={chart.name ?? undefined}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </main>
  );
}
