"use client";
import { useMemo, useState } from "react";
import { Plus, Wallet, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardContent, CardHeader, CardTitle, CardDescription, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useAiBudgets, useCreateAiBudget, useUpdateAiBudget, useDeleteAiBudget, useAiSpend, useAiCostBreakdown } from "@/lib/graphql/hooks";
import type { AiBudget, AiCostRollup, PatchAiBudgetInput } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

export default function AiBudgetsPage() {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<AiBudget | null>(null);
  const [toDelete, setToDelete] = useState<AiBudget | null>(null);
  const [spendLookup, setSpendLookup] = useState<{ scopeType: string; scopeRef: string } | null>(null);

  const query = useAiBudgets();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const create = useCreateAiBudget();
  const update = useUpdateAiBudget();
  const del = useDeleteAiBudget();
  const spend = useAiSpend(spendLookup?.scopeType ?? "", spendLookup?.scopeRef ?? "", undefined, { enabled: !!spendLookup });

  const columns: Column<AiBudget>[] = [
    { id: "scope", header: "Scope", cell: (b) => `${b.scopeType}/${b.scopeRef}` },
    { id: "window", header: "Window", width: 100, cell: (b) => b.window },
    { id: "limit", header: "Limit", width: 100, cell: (b) => `$${b.limitUsd.toFixed(2)}` },
    { id: "degrade", header: "Degrade at", width: 100, cell: (b) => `${b.degradePct}%` },
    { id: "status", header: "Status", width: 100, cell: (b) => <Badge variant={b.status === "active" ? "success" : "secondary"}>{b.status}</Badge> },
    { id: "createdAt", header: "Created", width: 170, cell: (b) => formatLocal(b.createdAt) },
    {
      id: "actions", header: "", width: 160,
      cell: (b) => (
        <div className="flex gap-1">
          <Button size="sm" variant="outline" onClick={() => setSpendLookup({ scopeType: b.scopeType, scopeRef: b.scopeRef })}>Spend</Button>
          <Can gate={FEATURE_GATES.manageAiBudgets}>
            <Button size="sm" variant="outline" onClick={() => { setEditing(b); setCreating(false); }}>Edit</Button>
          </Can>
          <Can gate={FEATURE_GATES.manageAiBudgets}>
            <Button size="sm" variant="ghost" onClick={() => setToDelete(b)}><Trash2 className="size-3" /></Button>
          </Can>
        </div>
      ),
    },
  ];

  const newButton = (
    <Can gate={FEATURE_GATES.manageAiBudgets}>
      <Button onClick={() => setCreating((v) => !v)}><Plus /> {creating ? "Cancel" : "New budget"}</Button>
    </Can>
  );

  return (
    <div>
      <PageHeader
        title="ai-gateway budgets"
        description="ai-gateway's OWN LLM-spend budgets — distinct from usage-service's platform-cost budgets."
        actions={newButton}
      />

      {creating && (
        <Card className="mb-4 border-primary/40">
          <CardContent className="pt-4">
            <NewBudgetForm pending={create.isPending} error={create.error} onCreate={(input) => create.mutate(input, { onSuccess: () => setCreating(false) })} />
          </CardContent>
        </Card>
      )}

      {editing && (
        <Card className="mb-4 border-primary/40">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-sm">Edit budget: {editing.scopeType}/{editing.scopeRef}</CardTitle>
            <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>Cancel</Button>
          </CardHeader>
          <CardContent>
            <EditBudgetForm
              budget={editing}
              pending={update.isPending}
              error={update.error}
              onSave={(input) => update.mutate({ id: editing.id, input }, { onSuccess: () => setEditing(null) })}
            />
          </CardContent>
        </Card>
      )}

      <CostBreakdownPanel />


      {spendLookup && (
        <Card className="mb-4">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-sm">Live spend: {spendLookup.scopeType}/{spendLookup.scopeRef}</CardTitle>
              <CardDescription>ai-gateway BudgetEngine.live_spend, real-time.</CardDescription>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setSpendLookup(null)}>Close</Button>
          </CardHeader>
          <CardContent>
            <AsyncBoundary isLoading={spend.isLoading} isError={spend.isError} error={spend.error} isEmpty={(spend.data ?? []).length === 0} emptyTitle="No spend rows">
              <div className="space-y-2 text-sm">
                {(spend.data ?? []).map((row) => (
                  <div key={row.budgetId} className="flex flex-wrap gap-3 rounded-md border p-2">
                    <span className="font-medium">{row.window}</span>
                    <span>spend ${row.spendUsd.toFixed(2)} / limit ${row.limitUsd.toFixed(2)}</span>
                    <span className="text-muted-foreground">reserved ${row.reservedUsd.toFixed(2)}</span>
                    <span className="text-muted-foreground">resets {formatLocal(row.resetAt)}</span>
                  </div>
                ))}
              </div>
            </AsyncBoundary>
          </CardContent>
        </Card>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No ai-gateway budgets configured"
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="ai-gateway budgets"
          rows={rows}
          columns={columns}
          rowId={(b) => b.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Wallet className="size-8" />
              <p>No budgets</p>
            </div>
          }
        />
      </AsyncBoundary>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title="Disable this budget?"
        description="Soft-deletes the budget (status set to disabled)."
        confirmLabel="Disable"
        destructive
        onConfirm={() => {
          if (toDelete) del.mutate(toDelete.id, { onSuccess: () => setToDelete(null) });
        }}
      />
    </div>
  );
}

// ADDED (provider-agnostic + cost-detail): real per-provider/model spend
// breakdown from the ledgered request_log — no estimated numbers.
const WINDOWS: { label: string; hours: number }[] = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
];

function RollupTable({ rows, first }: { rows: AiCostRollup[]; first: string }) {
  if (rows.length === 0) return <p className="text-xs text-muted-foreground">No usage in window.</p>;
  return (
    <table className="w-full text-sm">
      <thead className="text-xs text-muted-foreground">
        <tr className="text-left">
          <th className="py-1 pr-3 font-medium capitalize">{first}</th>
          <th className="py-1 pr-3 font-medium text-right">Requests</th>
          <th className="py-1 pr-3 font-medium text-right">In tok</th>
          <th className="py-1 pr-3 font-medium text-right">Out tok</th>
          <th className="py-1 font-medium text-right">Cost</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={`${r.provider}-${r.model}-${r.requestClass}-${i}`} className="border-t">
            <td className="py-1 pr-3">
              {first === "provider" && (r.provider ?? "—")}
              {first === "model" && (
                <span>
                  <span className="text-muted-foreground">{r.provider}</span> /{" "}
                  {r.model ?? r.modelAlias ?? "—"}
                </span>
              )}
              {first === "request class" && (r.requestClass ?? "—")}
            </td>
            <td className="py-1 pr-3 text-right tabular-nums">{r.requests.toLocaleString()}</td>
            <td className="py-1 pr-3 text-right tabular-nums">{r.inputTokens.toLocaleString()}</td>
            <td className="py-1 pr-3 text-right tabular-nums">{r.outputTokens.toLocaleString()}</td>
            <td className="py-1 text-right tabular-nums">${r.costUsd.toFixed(4)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CostBreakdownPanel() {
  const [hours, setHours] = useState(24);
  const q = useAiCostBreakdown(hours);
  const b = q.data;
  return (
    <Card className="mb-4">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="text-sm">Cost breakdown by provider &amp; model</CardTitle>
          <CardDescription>
            Real per-(provider, model, request-class) spend from the ledgered request log
            {b ? ` — price table ${b.window.priceVersion}` : ""}.
          </CardDescription>
        </div>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <Button key={w.hours} size="sm" variant={hours === w.hours ? "default" : "outline"} onClick={() => setHours(w.hours)}>
              {w.label}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={q.isLoading}
          isError={q.isError}
          error={q.error}
          isEmpty={!!b && b.totals.requests === 0}
          emptyTitle="No metered requests in this window"
          onRetry={() => q.refetch()}
        >
          {b && (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-4 rounded-md border p-3 text-sm">
                <span><span className="text-muted-foreground">Total cost</span> <span className="font-semibold">${b.totals.costUsd.toFixed(4)}</span></span>
                <span><span className="text-muted-foreground">Requests</span> {b.totals.requests.toLocaleString()}</span>
                <span><span className="text-muted-foreground">Input tokens</span> {b.totals.inputTokens.toLocaleString()}</span>
                <span><span className="text-muted-foreground">Output tokens</span> {b.totals.outputTokens.toLocaleString()}</span>
              </div>
              <div className="grid gap-6 md:grid-cols-3">
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">By provider</p>
                  <RollupTable rows={b.byProvider} first="provider" />
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">By model</p>
                  <RollupTable rows={b.byModel} first="model" />
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">By request class</p>
                  <RollupTable rows={b.byRequestClass} first="request class" />
                </div>
              </div>
            </div>
          )}
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}

function NewBudgetForm({
  onCreate,
  pending,
  error,
}: {
  onCreate: (input: { scopeType: string; scopeRef: string; window: string; limitUsd: number; degradePct?: number }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [scopeType, setScopeType] = useState("tenant");
  const [scopeRef, setScopeRef] = useState("");
  const [window, setWindow] = useState("monthly");
  const [limitUsd, setLimitUsd] = useState("");
  const [degradePct, setDegradePct] = useState("95");

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (scopeRef.trim() && limitUsd.trim()) {
          onCreate({ scopeType, scopeRef: scopeRef.trim(), window, limitUsd: Number(limitUsd), degradePct: degradePct.trim() ? Number(degradePct) : undefined });
        }
      }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="b-scope-type">Scope type</Label>
        <select id="b-scope-type" value={scopeType} onChange={(e) => setScopeType(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
          <option value="platform">platform</option>
          <option value="tenant">tenant</option>
          <option value="workspace">workspace</option>
          <option value="principal">principal</option>
          <option value="virtual_key">virtual_key</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="b-scope-ref">Scope ref</Label>
        <Input id="b-scope-ref" value={scopeRef} onChange={(e) => setScopeRef(e.target.value)} placeholder="tenant id / workspace id / ..." className="h-9 w-56" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="b-window">Window</Label>
        <select id="b-window" value={window} onChange={(e) => setWindow(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
          <option value="daily">daily</option>
          <option value="monthly">monthly</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="b-limit">Limit (USD)</Label>
        <Input id="b-limit" type="number" min="0" step="0.01" value={limitUsd} onChange={(e) => setLimitUsd(e.target.value)} className="h-9 w-32" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="b-degrade">Degrade at %</Label>
        <Input id="b-degrade" type="number" min="1" max="100" value={degradePct} onChange={(e) => setDegradePct(e.target.value)} className="h-9 w-24" />
      </div>
      <Button type="submit" disabled={pending}>Create</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}

function EditBudgetForm({
  budget,
  onSave,
  pending,
  error,
}: {
  budget: AiBudget;
  onSave: (input: PatchAiBudgetInput) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [limitUsd, setLimitUsd] = useState(String(budget.limitUsd));
  const [degradePct, setDegradePct] = useState(String(budget.degradePct));
  const [status, setStatus] = useState(budget.status);

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({
          limitUsd: limitUsd.trim() ? Number(limitUsd) : undefined,
          degradePct: degradePct.trim() ? Number(degradePct) : undefined,
          status,
        });
      }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="e-limit">Limit (USD)</Label>
        <Input id="e-limit" type="number" min="0" step="0.01" value={limitUsd} onChange={(e) => setLimitUsd(e.target.value)} className="h-9 w-32" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="e-degrade">Degrade at %</Label>
        <Input id="e-degrade" type="number" min="1" max="100" value={degradePct} onChange={(e) => setDegradePct(e.target.value)} className="h-9 w-24" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="e-status">Status</Label>
        <select id="e-status" value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
          <option value="active">active</option>
          <option value="disabled">disabled</option>
        </select>
      </div>
      <Button type="submit" disabled={pending}>Save</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}
