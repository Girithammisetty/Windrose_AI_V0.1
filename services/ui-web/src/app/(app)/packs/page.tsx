"use client";
import { useMemo, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Button } from "@/components/ui/button";
import Link from "next/link";
import {
  usePacks, usePack, usePackInstalls, usePlanPackInstall, useInstallPack, useUninstallPack,
  useCompletePackInstall,
} from "@/lib/graphql/hooks";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useSession } from "@/lib/session/SessionContext";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { Pack, PackInstall, PackPlanOp, PackLedgerRow } from "@/lib/graphql/types";

/**
 * Capability packs (BRD 23). Browse the catalog of vertical solutions and
 * install one into this workspace through the governed pack-service: a dry-run
 * plan first (no side effects), then execute — which materializes the pack's
 * components AS you (semantic models, dashboards, case taxonomy, roles,
 * decision tables) and records an origin-tagged ledger you can reverse. Nothing
 * is faked: components Core cannot materialize yet are shown as deferred.
 */
export default function PacksPage() {
  const { can } = useCapabilities();
  const canInstall = can(FEATURE_GATES.installPack);
  const { workspaceId } = useSession();
  const packs = usePacks();
  const installs = usePackInstalls(workspaceId);
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div>
      <PageHeader
        title="Capability Packs"
        description="Install a full vertical solution as one governed bundle. A dry-run plan previews every change; installing materializes the pack into this workspace and records a reversible, origin-tagged ledger. Components Core can't materialize yet are shown honestly as deferred."
      />

      <InstalledSection installs={installs} workspaceId={workspaceId} canInstall={canInstall} />

      <h2 className="mb-3 mt-6 text-sm font-semibold text-muted-foreground">Catalog</h2>
      <AsyncBoundary
        isLoading={packs.isLoading}
        isError={packs.isError}
        error={packs.error}
        isEmpty={!packs.isLoading && (packs.data ?? []).length === 0}
        emptyTitle="No packs in the catalog."
        onRetry={() => packs.refetch()}
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(packs.data ?? []).map((p) => (
            <PackCard key={p.name} pack={p} expanded={open === p.name}
              onToggle={() => setOpen((v) => (v === p.name ? null : p.name))}
              workspaceId={workspaceId} canInstall={canInstall}
              onInstalled={() => { installs.refetch(); }} />
          ))}
        </div>
      </AsyncBoundary>
    </div>
  );
}

// ---- installed packs --------------------------------------------------------

function InstalledSection({ installs, workspaceId, canInstall }:
  { installs: ReturnType<typeof usePackInstalls>; workspaceId: string; canInstall: boolean }) {
  const rows = (installs.data ?? []).filter((i) => i.status !== "uninstalled");
  if (rows.length === 0) return null;
  return (
    <div className="mb-2">
      <h2 className="mb-3 text-sm font-semibold text-muted-foreground">Installed in this workspace</h2>
      <div className="flex flex-col gap-2">
        {rows.map((i) => (
          <InstalledRow key={i.id} install={i} workspaceId={workspaceId} canInstall={canInstall} />
        ))}
      </div>
    </div>
  );
}

function InstalledRow({ install, workspaceId, canInstall }:
  { install: PackInstall; workspaceId: string; canInstall: boolean }) {
  const uninstall = useUninstallPack(workspaceId);
  const complete = useCompletePackInstall(workspaceId);
  const [result, setResult] = useState<{ reversed: number; tombstoned: number } | null>(null);
  const s = install.summary ?? {};
  const awaiting = install.status === "awaiting_approval";
  const completeErr = complete.error instanceof GraphQLRequestError ? complete.error : null;

  return (
    <div className="rounded-lg border p-3" data-testid="pack-install-row">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{install.pack}</span>
          <span className="text-xs text-muted-foreground">v{install.version}</span>
          <StatusChip status={install.status} />
          <span className="text-xs text-muted-foreground">
            {s.created ?? 0} created
            {s.submitted ? ` · ${s.submitted} awaiting approval` : ""}
            {s.dashboards ? ` · ${s.dashboards} dashboards` : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {canInstall && awaiting && (
            <Button size="sm" disabled={complete.isPending}
              onClick={() => complete.mutate(install.id)}>
              {complete.isPending ? "Completing…" : "Complete install"}
            </Button>
          )}
          {canInstall && install.status !== "uninstalled" && (
            <Button size="sm" variant="outline" disabled={uninstall.isPending}
              onClick={() => uninstall.mutate(install.id, { onSuccess: (r) => setResult(r) })}>
              {uninstall.isPending ? "Uninstalling…" : "Uninstall"}
            </Button>
          )}
        </div>
      </div>

      {awaiting && (
        <p className="mt-2 text-xs text-muted-foreground" data-testid="pack-awaiting">
          The pack&apos;s semantic model is submitted for four-eyes review — a steward must{" "}
          <Link href="/data/semantic-models" className="text-primary underline">approve it</Link>{" "}
          before its dashboards can materialize. Then click <em>Complete install</em>.
        </p>
      )}
      {completeErr && <p role="alert" className="mt-2 text-xs text-destructive">{completeErr.message}</p>}
      {result && (
        <p className="mt-2 text-xs text-muted-foreground" data-testid="pack-uninstall-result">
          Reversed {result.reversed} object{result.reversed === 1 ? "" : "s"} · {result.tombstoned} tombstoned
          (retained — Core has no revert verb for those kinds).
        </p>
      )}
    </div>
  );
}

// ---- one catalog card -------------------------------------------------------

function PackCard({ pack, expanded, onToggle, workspaceId, canInstall, onInstalled }: {
  pack: Pack; expanded: boolean; onToggle: () => void;
  workspaceId: string; canInstall: boolean; onInstalled: () => void;
}) {
  const totalComponents = pack.components.reduce((n, c) => n + c.count, 0);
  return (
    <div className="flex flex-col rounded-lg border p-4" data-testid="pack-card">
      <div className="mb-1 flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold">{pack.name}</h3>
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">v{pack.version}</span>
      </div>
      <p className="mb-2 line-clamp-3 text-xs text-muted-foreground">{pack.description}</p>
      <div className="mb-3 flex flex-wrap gap-1">
        {pack.categories.slice(0, 4).map((c) => (
          <span key={c} className="rounded bg-accent px-1.5 py-0.5 text-[10px] text-accent-foreground">{c}</span>
        ))}
      </div>
      <p className="mb-3 text-[11px] text-muted-foreground">
        {totalComponents} components · {pack.deferredKinds.length} deferred kinds
      </p>
      <div className="mt-auto">
        <Button size="sm" variant="ghost" onClick={onToggle}>
          {expanded ? "Hide" : "Details & install"}
        </Button>
      </div>
      {expanded && (
        <PackDetail name={pack.name} workspaceId={workspaceId}
          canInstall={canInstall} onInstalled={onInstalled} />
      )}
    </div>
  );
}

function PackDetail({ name, workspaceId, canInstall, onInstalled }:
  { name: string; workspaceId: string; canInstall: boolean; onInstalled: () => void }) {
  const detail = usePack(name);
  const plan = usePlanPackInstall();
  const install = useInstallPack();
  const [planned, setPlanned] = useState<PackPlanOp[] | null>(null);
  const [ledger, setLedger] = useState<PackLedgerRow[] | null>(null);
  const err = [plan.error, install.error].find((e) => e instanceof GraphQLRequestError) as GraphQLRequestError | undefined;
  const d = detail.data;

  const runPlan = () =>
    plan.mutate({ pack: name, workspaceId }, { onSuccess: (r) => { setPlanned(r.plan); setLedger(null); } });
  const runInstall = () =>
    install.mutate({ pack: name, workspaceId }, {
      onSuccess: (r) => { setLedger(r.ledger ?? []); setPlanned(null); onInstalled(); },
    });

  return (
    <div className="mt-3 border-t pt-3 text-xs">
      {detail.isLoading && <p className="text-muted-foreground">Loading manifest…</p>}
      {d && (
        <>
          <div className="mb-2 flex flex-wrap gap-1">
            {d.components.map((c) => (
              <span key={c.kind} className="rounded bg-muted px-1.5 py-0.5 font-mono">
                {c.kind}×{c.count}
              </span>
            ))}
          </div>
          {(d.deferred ?? []).length > 0 && (
            <details className="mb-2">
              <summary className="cursor-pointer text-muted-foreground">
                {d.deferred!.length} deferred component{d.deferred!.length === 1 ? "" : "s"} (honest — not faked)
              </summary>
              <ul className="mt-1 flex flex-col gap-1 pl-1">
                {d.deferred!.map((x) => (
                  <li key={x.kind}><span className="font-mono">{x.kind}</span> — <span className="text-muted-foreground">{x.reason}</span></li>
                ))}
              </ul>
            </details>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" disabled={plan.isPending} onClick={runPlan}>
              {plan.isPending ? "Planning…" : "Dry-run plan"}
            </Button>
            {canInstall && (
              <Button size="sm" disabled={install.isPending} onClick={runInstall}>
                {install.isPending ? "Installing…" : "Install into this workspace"}
              </Button>
            )}
          </div>
        </>
      )}

      {err && <p role="alert" className="mt-2 text-destructive">{err.message}</p>}
      {planned && <PlanView plan={planned} />}
      {ledger && <LedgerView ledger={ledger} />}
    </div>
  );
}

function PlanView({ plan }: { plan: PackPlanOp[] }) {
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const o of plan) c[o.action] = (c[o.action] ?? 0) + 1;
    return c;
  }, [plan]);
  return (
    <div className="mt-2 rounded-md border bg-muted/40 p-2" data-testid="pack-plan">
      <p className="mb-1 font-medium">
        Plan: {counts.create ?? 0} create · {counts.exists ?? 0} already present
        {counts.after_approval ? ` · ${counts.after_approval} after approval` : ""}
        {counts.deferred ? ` · ${counts.deferred} deferred` : ""}
      </p>
      <div className="max-h-40 overflow-y-auto">
        {plan.filter((o) => o.action === "create" || o.action === "exists").map((o, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className={o.action === "create" ? "text-primary" : "text-muted-foreground"}>{o.action}</span>
            <span className="font-mono">{o.kind}</span>
            <span className="truncate text-muted-foreground">{o.name ?? o.identity}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function LedgerView({ ledger }: { ledger: PackLedgerRow[] }) {
  const created = ledger.filter((r) => r.action === "create");
  const reversible = created.filter((r) => r.reversible).length;
  return (
    <div className="mt-2 rounded-md border bg-muted/40 p-2" data-testid="pack-ledger">
      <p className="mb-1 font-medium">
        Installed ✓ — {created.length} object{created.length === 1 ? "" : "s"} materialized ({reversible} reversible)
      </p>
      <div className="max-h-40 overflow-y-auto">
        {ledger.map((r) => (
          <div key={r.id} className="flex items-center gap-2">
            <StatusChip status={r.action === "create" ? "success" : "secondary"} />
            <span className="font-mono">{r.kind}</span>
            <span className="truncate">{r.identity}</span>
            {r.reversible && <span className="text-[10px] text-muted-foreground">reversible</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
