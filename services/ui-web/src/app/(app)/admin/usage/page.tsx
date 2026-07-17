"use client";
import { useMemo, useState } from "react";
import { Wallet, Receipt, X, TrendingUp } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { CostPanel } from "@/components/usage/CostPanel";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardContent, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useSession } from "@/lib/session/SessionContext";
import {
  useBudgets,
  useCreateBudget,
  useUpdateBudget,
  useDeleteBudget,
  useRateCards,
  useCreateRateCard,
  useActivateRateCard,
  useAnomalies,
  useDismissAnomaly,
} from "@/lib/graphql/hooks";
import type { Budget, RateCard, Anomaly } from "@/lib/graphql/types";
import { formatUsd, formatLocal } from "@/lib/utils";

const WINDOWS = ["calendar_month", "calendar_day", "rolling_7d"];
const ACTIONS = ["alert_only", "hard_stop"];

export default function AdminUsagePage() {
  const { workspaceId } = useSession();
  return (
    <div>
      <PageHeader
        title="Usage & budgets"
        description="Per-workspace AI spend with live budget thresholds (80/95/100)."
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <CostPanel workspaceId={workspaceId} />
        <BudgetsCard workspaceId={workspaceId} />
      </div>
      <div className="mt-4">
        <RateCardsCard />
      </div>
      <div className="mt-4">
        <AnomaliesCard />
      </div>
    </div>
  );
}

function AnomaliesCard() {
  const [status, setStatus] = useState("open");
  const query = useAnomalies(status || undefined);
  const dismiss = useDismissAnomaly();
  const rows = query.data ?? [];

  const columns: Column<Anomaly>[] = [
    { id: "meter", header: "Meter", cell: (a) => <span className="font-medium">{a.meterKey}</span> },
    { id: "day", header: "Day", width: 110, cell: (a) => a.day },
    { id: "observed", header: "Observed", width: 100, cell: (a) => formatUsd(a.observed) },
    { id: "mean", header: "Mean", width: 100, cell: (a) => formatUsd(a.mean) },
    { id: "z", header: "z-score", width: 90, cell: (a) => <span className="font-mono">{a.z.toFixed(2)}</span> },
    { id: "status", header: "Status", width: 110, cell: (a) => <Badge variant={a.status === "open" ? "warning" : "secondary"}>{a.status}</Badge> },
    { id: "since", header: "Detected", width: 170, cell: (a) => formatLocal(a.createdAt) },
    {
      id: "actions", header: "", width: 100,
      cell: (a) =>
        a.status === "open" ? (
          <Can gate={FEATURE_GATES.dismissAnomaly}>
            <Button
              size="sm" variant="ghost" disabled={dismiss.isPending}
              onClick={() => dismiss.mutate(a.id)}
            >
              Dismiss
            </Button>
          </Can>
        ) : (
          a.dismissedBy ? <span className="text-xs text-muted-foreground">by {a.dismissedBy}</span> : null
        ),
    },
  ];

  return (
    <Can gate={FEATURE_GATES.viewAnomalies} fallback={null}>
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="flex items-center gap-2 text-sm"><TrendingUp className="size-4" aria-hidden />Spend anomalies</CardTitle>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Anomaly status"
            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          >
            <option value="open">open</option>
            <option value="dismissed">dismissed</option>
            <option value="">all</option>
          </select>
        </CardHeader>
        <CardContent>
          <AsyncBoundary
            isLoading={query.isLoading}
            isError={query.isError}
            error={query.error}
            isEmpty={rows.length === 0}
            emptyTitle="No anomalies detected."
            onRetry={() => query.refetch()}
          >
            <DataTable ariaLabel="Spend anomalies" rows={rows} columns={columns} rowId={(a) => a.id} />
          </AsyncBoundary>
        </CardContent>
      </Card>
    </Can>
  );
}

function BudgetsCard({ workspaceId }: { workspaceId: string }) {
  const query = useBudgets();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [selected, setSelected] = useState<Budget | null>(null);
  const [creating, setCreating] = useState(false);
  const createBudget = useCreateBudget();

  const columns: Column<Budget>[] = [
    { id: "meter", header: "Meter", cell: (b) => <span className="font-medium">{b.meterKey}</span> },
    { id: "scope", header: "Scope", cell: (b) => <span className="font-mono text-xs">{b.scope ?? "tenant"}</span> },
    { id: "window", header: "Window", width: 130, cell: (b) => b.window },
    { id: "limit", header: "Limit", width: 100, cell: (b) => formatUsd(b.limitUsd ?? 0) },
    { id: "action", header: "At 100%", width: 110, cell: (b) => <Badge variant={b.actionAt100 === "hard_stop" ? "warning" : "secondary"}>{b.actionAt100}</Badge> },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">Budget administration</CardTitle>
        <Can gate={FEATURE_GATES.createBudget}>
          <Button size="sm" onClick={() => setCreating((v) => !v)}>{creating ? "Cancel" : "New budget"}</Button>
        </Can>
      </CardHeader>
      <CardContent className="space-y-3">
        {creating && (
          <NewBudgetForm
            defaultWorkspaceId={workspaceId}
            onCreate={(input) =>
              createBudget.mutate(input, { onSuccess: (r) => { setCreating(false); setSelected(r.createBudget); } })
            }
            pending={createBudget.isPending}
            error={createBudget.error}
          />
        )}

        <div className="grid gap-3 lg:grid-cols-[1fr_320px]">
          <AsyncBoundary
            isLoading={query.isLoading}
            isError={query.isError}
            error={query.error}
            isEmpty={rows.length === 0}
            emptyTitle="No budgets yet."
            onRetry={() => query.refetch()}
          >
            <DataTable
              ariaLabel="Budgets"
              rows={rows}
              columns={columns}
              rowId={(b) => b.id}
              onRowActivate={(b) => setSelected(b)}
              hasMore={query.hasNextPage}
              isFetchingMore={query.isFetchingNextPage}
              onLoadMore={() => query.fetchNextPage()}
            />
          </AsyncBoundary>
          <BudgetDetail budget={selected} onClose={() => setSelected(null)} onDeleted={() => setSelected(null)} />
        </div>
      </CardContent>
    </Card>
  );
}

function NewBudgetForm({
  defaultWorkspaceId,
  onCreate,
  pending,
  error,
}: {
  defaultWorkspaceId: string;
  onCreate: (input: { workspaceId?: string; meterKey: string; window: string; limitUsd: number; actionAt100: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [workspaceId, setWorkspaceId] = useState(defaultWorkspaceId);
  const [meterKey, setMeterKey] = useState("usd_total");
  const [window, setWindowVal] = useState(WINDOWS[0]);
  const [limitUsd, setLimitUsd] = useState("100");
  const [actionAt100, setActionAt100] = useState(ACTIONS[0]);

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-md border bg-muted/30 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const limit = Number(limitUsd);
        if (meterKey.trim() && limit > 0)
          onCreate({ workspaceId: workspaceId || undefined, meterKey: meterKey.trim(), window, limitUsd: limit, actionAt100 });
      }}
    >
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Workspace id</span>
        <Input value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)} aria-label="Budget workspace id" className="h-8 w-48 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Meter key</span>
        <Input value={meterKey} onChange={(e) => setMeterKey(e.target.value)} aria-label="Meter key" className="h-8 w-32 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Window</span>
        <select value={window} onChange={(e) => setWindowVal(e.target.value)} aria-label="Window" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
          {WINDOWS.map((w) => <option key={w} value={w}>{w}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Limit (USD)</span>
        <Input type="number" min="0" step="0.01" value={limitUsd} onChange={(e) => setLimitUsd(e.target.value)} aria-label="Limit USD" className="h-8 w-24 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">At 100%</span>
        <select value={actionAt100} onChange={(e) => setActionAt100(e.target.value)} aria-label="Action at 100 percent" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
          {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </label>
      <Button type="submit" size="sm" disabled={pending}>Create</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}

function BudgetDetail({
  budget,
  onClose,
  onDeleted,
}: {
  budget: Budget | null;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const updateBudget = useUpdateBudget();
  const deleteBudget = useDeleteBudget();
  const [limitUsd, setLimitUsd] = useState(String(budget?.limitUsd ?? ""));
  const [actionAt100, setActionAt100] = useState(budget?.actionAt100 ?? ACTIONS[0]);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  if (!budget) {
    return (
      <Card className="h-fit">
        <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
          <Wallet className="size-6" aria-hidden />
          <p>Select a budget to edit it.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-fit">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">{budget.meterKey}</CardTitle>
        <div className="flex items-center gap-1">
          <Can gate={FEATURE_GATES.deleteBudget}>
            <Button variant="ghost" size="sm" onClick={() => setConfirmingDelete(true)}>Delete</Button>
          </Can>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">
          scope {budget.scope ?? "tenant"} · window {budget.window} · status {budget.status}
        </p>
        <Can gate={FEATURE_GATES.updateBudget}>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const limit = Number(limitUsd);
              updateBudget.mutate({ id: budget.id, input: { limitUsd: limit > 0 ? limit : undefined, actionAt100 } });
            }}
          >
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Limit (USD)</span>
              <Input type="number" min="0" step="0.01" value={limitUsd} onChange={(e) => setLimitUsd(e.target.value)} aria-label="Edit limit USD" className="h-8 w-28 text-xs" />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">At 100%</span>
              <select value={actionAt100 ?? ACTIONS[0]} onChange={(e) => setActionAt100(e.target.value)} aria-label="Edit action at 100 percent" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
                {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            <Button type="submit" size="sm" disabled={updateBudget.isPending}>Save</Button>
          </form>
          {updateBudget.error && <p className="text-xs text-destructive">{updateBudget.error.message}</p>}
        </Can>
      </CardContent>

      <ConfirmDialog
        open={confirmingDelete}
        onOpenChange={setConfirmingDelete}
        title="Delete budget"
        description={`Delete the budget on "${budget.meterKey}"? This cannot be undone.`}
        confirmLabel="Delete"
        destructive
        onConfirm={() => {
          deleteBudget.mutate(budget.id, { onSuccess: onDeleted, onSettled: () => setConfirmingDelete(false) });
        }}
      />
    </Card>
  );
}

function RateCardsCard() {
  const query = useRateCards();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [selected, setSelected] = useState<RateCard | null>(null);
  const [creating, setCreating] = useState(false);
  const createRateCard = useCreateRateCard();
  const activateRateCard = useActivateRateCard();

  const columns: Column<RateCard>[] = [
    { id: "version", header: "Version", width: 100, cell: (rc) => <span className="font-medium">v{rc.version}</span> },
    { id: "effective", header: "Effective from", width: 150, cell: (rc) => rc.effectiveFrom },
    { id: "status", header: "Status", width: 130, cell: (rc) => <Badge variant={rc.status === "active" ? "success" : rc.status === "draft" ? "secondary" : "warning"}>{rc.status}</Badge> },
    { id: "items", header: "Priced meters", cell: (rc) => Object.keys((rc.items as Record<string, number>) ?? {}).length },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">Rate cards</CardTitle>
        <Can gate={FEATURE_GATES.createRateCard}>
          <Button size="sm" onClick={() => setCreating((v) => !v)}>{creating ? "Cancel" : "New rate card"}</Button>
        </Can>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Create/activate are platform-only (usage-service PlatformOnly actions) — hidden here unless the
          signed-in principal carries a platform-operator token, per rbac (a tenant admin gets a real 403 if forced).
        </p>
        {creating && (
          <NewRateCardForm
            onCreate={(input) =>
              createRateCard.mutate(input, { onSuccess: (r) => { setCreating(false); setSelected(r.createRateCard); } })
            }
            pending={createRateCard.isPending}
            error={createRateCard.error}
          />
        )}

        <div className="grid gap-3 lg:grid-cols-[1fr_320px]">
          <AsyncBoundary
            isLoading={query.isLoading}
            isError={query.isError}
            error={query.error}
            isEmpty={rows.length === 0}
            emptyTitle="No rate cards yet."
            onRetry={() => query.refetch()}
          >
            <DataTable
              ariaLabel="Rate cards"
              rows={rows}
              columns={columns}
              rowId={(rc) => rc.id}
              onRowActivate={(rc) => setSelected(rc)}
              hasMore={query.hasNextPage}
              isFetchingMore={query.isFetchingNextPage}
              onLoadMore={() => query.fetchNextPage()}
            />
          </AsyncBoundary>

          {selected ? (
            <Card className="h-fit">
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-sm">v{selected.version} · {selected.status}</CardTitle>
                <Button variant="ghost" size="sm" onClick={() => setSelected(null)} aria-label="Close"><X className="size-4" /></Button>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-xs text-muted-foreground">effective {selected.effectiveFrom}</p>
                <ul className="space-y-1 text-xs">
                  {Object.entries((selected.items as Record<string, number>) ?? {}).map(([k, v]) => (
                    <li key={k} className="flex justify-between font-mono"><span>{k}</span><span>{formatUsd(v)}</span></li>
                  ))}
                </ul>
                {selected.status === "draft" && (
                  <Can gate={FEATURE_GATES.activateRateCard}>
                    <Button
                      size="sm"
                      disabled={activateRateCard.isPending}
                      onClick={() => activateRateCard.mutate(selected.id, { onSuccess: (r) => setSelected(r.activateRateCard) })}
                    >
                      Activate
                    </Button>
                  </Can>
                )}
                {activateRateCard.error && <p className="text-xs text-destructive">{activateRateCard.error.message}</p>}
              </CardContent>
            </Card>
          ) : (
            <Card className="h-fit">
              <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
                <Receipt className="size-6" aria-hidden />
                <p>Select a rate card to view its priced meters.</p>
              </CardContent>
            </Card>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function NewRateCardForm({
  onCreate,
  pending,
  error,
}: {
  onCreate: (input: { version: number; effectiveFrom: string; items: Record<string, number> }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [version, setVersion] = useState("1");
  const [effectiveFrom, setEffectiveFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [itemsText, setItemsText] = useState("api_calls=0.001");

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-md border bg-muted/30 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const items: Record<string, number> = {};
        for (const pair of itemsText.split(",")) {
          const [k, v] = pair.split("=").map((s) => s.trim());
          if (k && v && !Number.isNaN(Number(v))) items[k] = Number(v);
        }
        const v = Number(version);
        if (v > 0 && effectiveFrom && Object.keys(items).length > 0) onCreate({ version: v, effectiveFrom, items });
      }}
    >
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Version</span>
        <Input type="number" min="1" value={version} onChange={(e) => setVersion(e.target.value)} aria-label="Rate card version" className="h-8 w-20 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Effective from</span>
        <Input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} aria-label="Effective from" className="h-8 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Items (meter=price, comma-separated)</span>
        <Input value={itemsText} onChange={(e) => setItemsText(e.target.value)} aria-label="Rate card items" className="h-8 w-64 text-xs" />
      </label>
      <Button type="submit" size="sm" disabled={pending}>Create</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}
