"use client";
import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { useCostPanel } from "@/lib/graphql/hooks";
import { formatUsd } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";
import type { BudgetState } from "@/lib/graphql/types";

function range(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getTime() - 30 * 86400_000);
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

/**
 * Per-workspace AI cost panel (UI-FR-035, AC-8). Spend by meter, top consumers,
 * and live budget states with 80/95/100 threshold indicators. Task #78: the
 * "usage.events.v1" subscription here was a tenant/workspace-wide broadcast
 * with no matching realtime-hub scheme (grammar only routes to one resource)
 * — removed; this panel refetches on its own polling/refresh path instead.
 * Re-add once a tenant-broadcast scheme exists (follow-up to #78).
 */
export function CostPanel({ workspaceId }: { workspaceId: string }) {
  const { from, to } = useMemo(range, []);
  const query = useCostPanel(workspaceId, from, to);

  const panel = query.data?.workspaceCostPanel;
  const totalCost = panel?.rows.reduce((n, r) => n + (r.costUsd ?? 0), 0) ?? 0;

  return (
    <Card data-cost-panel={workspaceId}>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-sm">{t("cost.title")}</CardTitle>
        <span className="text-lg font-semibold">{formatUsd(totalCost)}</span>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={!query.isLoading && (panel?.rows.length ?? 0) === 0 && (panel?.budgetStates.length ?? 0) === 0}
          emptyTitle="No usage in this period"
          onRetry={() => query.refetch()}
        >
          <div className="space-y-4">
            <div>
              <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">{t("cost.budget")}</p>
              <div className="space-y-2">
                {panel?.budgetStates.map((b, i) => (
                  <BudgetBar key={b.scope ?? i} budget={b} />
                ))}
                {panel?.budgetStates.length === 0 && <p className="text-xs text-muted-foreground">No budgets set.</p>}
              </div>
            </div>

            <div>
              <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">{t("cost.spend")} by meter</p>
              <div className="space-y-1">
                {panel?.rows.slice(0, 6).map((r, i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <span className="truncate font-mono text-xs">{r.meterKey}</span>
                    <span>{formatUsd(r.costUsd)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}

function BudgetBar({ budget }: { budget: BudgetState }) {
  const consumed = budget.consumed ?? 0;
  const limit = budget.limit ?? 0;
  const pct = limit > 0 ? Math.min(100, (consumed / limit) * 100) : 0;
  const threshold = budget.lastThreshold ?? (pct >= 100 ? 100 : pct >= 95 ? 95 : pct >= 80 ? 80 : 0);
  const color =
    pct >= 100 ? "bg-destructive" : pct >= 95 ? "bg-[hsl(var(--warning))]" : pct >= 80 ? "bg-[hsl(var(--warning))]" : "bg-primary";
  const exhausted = budget.exhaustedAt != null || (limit > 0 && consumed >= limit);

  return (
    <div data-budget-scope={budget.scope} data-threshold={threshold} data-exhausted={exhausted ? "true" : "false"}>
      <div className="flex justify-between text-xs">
        <span className="truncate">{budget.scope ?? "workspace"}</span>
        <span className={exhausted ? "font-semibold text-destructive" : ""}>
          {formatUsd(consumed)} / {formatUsd(limit)}
          {threshold >= 80 && ` · ${threshold}%`}
        </span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={Math.round(pct)}>
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
