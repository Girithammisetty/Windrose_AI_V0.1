"use client";
import { useMemo, useRef, useState, useEffect } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useSearchParams } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { AiLabel } from "@/components/primitives/AiLabel";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/primitives";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { ProposalDetail } from "@/components/inbox/ProposalDetail";
import { useProposalsInbox, useDecideProposal } from "@/lib/graphql/hooks";
import { useToasts } from "@/stores/ui";
import {
  isBulkApprovable,
  isDestructiveTool,
  resolveBulkSelection,
  summarizeByTool,
  riskOf,
} from "@/lib/agentic/proposals";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";
import type { Proposal } from "@/lib/graphql/types";

export default function InboxPage() {
  const params = useSearchParams();
  const query = useProposalsInbox({ status: "PENDING" });
  // Task #78: list-wide "any proposal of mine" subscription — no such
  // broadcast scheme exists in realtime-hub (grammar routes to one resource
  // only), and "ai.proposal.expired" isn't even an event agent-runtime emits.
  // Removed; the inbox already refetches via query invalidation elsewhere.
  const decide = useDecideProposal();
  const push = useToasts((s) => s.push);

  const proposals = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [confirmBulk, setConfirmBulk] = useState(false);

  // Deep-link to a proposal (from copilot suggested action / case tab).
  useEffect(() => {
    const p = params.get("p");
    if (p) setActiveId(p);
  }, [params]);

  const active = proposals.find((p) => p.id === activeId) ?? proposals[0] ?? null;

  // Virtualized list (UI-FR-033): same TanStack Virtual windowing as DataTable /
  // TraceVisualizer — bounded DOM rows regardless of inbox size.
  const listRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: proposals.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 92,
    overscan: 8,
  });
  const virtualItems = virtualizer.getVirtualItems();

  // Cursor infinite-load: fetch more as the window nears the end of the list.
  useEffect(() => {
    const last = virtualItems[virtualItems.length - 1];
    if (!last) return;
    if (query.hasNextPage && !query.isFetchingNextPage && last.index >= proposals.length - 5) {
      query.fetchNextPage();
    }
  }, [virtualItems, query, proposals.length]);

  const bulk = resolveBulkSelection(proposals, selected);
  const toolSummary = summarizeByTool(proposals, bulk.approvable);

  function toggle(p: Proposal) {
    // Destructive/high-risk are UNSELECTABLE by construction (BR-3, AC-5).
    if (!isBulkApprovable(p)) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(p.id)) next.delete(p.id);
      else next.add(p.id);
      return next;
    });
  }

  async function runBulk() {
    setConfirmBulk(false);
    for (const id of bulk.approvable) {
      try {
        await decide.mutateAsync({ id, decision: { kind: "APPROVE" } });
      } catch {
        /* per-item failure surfaces via badge reconciliation */
      }
    }
    push({ title: `Approved ${bulk.approvable.length} proposals`, variant: "success" });
    setSelected(new Set());
  }

  return (
    <div>
      <PageHeader
        title={t("inbox.title")}
        description={t("inbox.pending", { count: proposals.length })}
        actions={
          <div className="flex items-center gap-2">
            {selected.size > 0 && (
              <span className="text-xs text-muted-foreground">
                {bulk.approvable.length} approvable{bulk.excluded.length ? `, ${bulk.excluded.length} excluded` : ""}
              </span>
            )}
            <Button
              variant="ai"
              size="sm"
              disabled={bulk.approvable.length === 0}
              onClick={() => setConfirmBulk(true)}
              data-testid="bulk-approve"
            >
              {t("action.bulkApprove")}
            </Button>
          </div>
        }
      />

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={proposals.length === 0}
        emptyTitle="No pending proposals"
        onRetry={() => query.refetch()}
      >
        <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
          {/* List — windowed virtualization (UI-FR-033); bounded DOM rows. */}
          <div
            ref={listRef}
            className="max-h-[calc(100vh-12rem)] overflow-auto pr-1"
            role="list"
            aria-label="Proposals"
          >
            <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
              {virtualItems.map((vi) => {
                const p = proposals[vi.index];
                const destructive = isDestructiveTool(p.tool);
                const risk = riskOf(p);
                const checkable = isBulkApprovable(p);
                return (
                  <div
                    key={p.id}
                    data-index={vi.index}
                    ref={virtualizer.measureElement}
                    className="absolute left-0 top-0 w-full pb-2"
                    style={{ transform: `translateY(${vi.start}px)` }}
                  >
                    <Card
                      role="listitem"
                      onClick={() => setActiveId(p.id)}
                      className={cn(
                        "cursor-pointer p-3 transition-colors hover:bg-accent/50",
                        active?.id === p.id && "ring-2 ring-primary",
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          aria-label={`Select ${p.tool}`}
                          disabled={!checkable}
                          checked={selected.has(p.id)}
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => toggle(p)}
                          className="size-4 accent-[hsl(var(--ai))] disabled:opacity-40"
                          data-destructive={destructive ? "true" : "false"}
                        />
                        <AiLabel />
                        <span className="truncate text-sm font-medium">{p.tool}</span>
                        {risk.risk === "high" && (
                          <span
                            className="ml-auto inline-flex items-center gap-1 text-xs text-destructive"
                            title={t("inbox.bulkExcludesDestructive")}
                          >
                            <AlertTriangle className="size-3" /> {risk.reason}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 truncate text-xs text-muted-foreground">{p.rationale ?? p.predictedEffect?.summary}</p>
                      <p className="mt-1 text-[11px] text-muted-foreground">{p.agentKey}</p>
                    </Card>
                  </div>
                );
              })}
            </div>
            {query.isFetchingNextPage && (
              <p className="py-2 text-center text-xs text-muted-foreground">{t("action.loadMore")}…</p>
            )}
          </div>

          {/* Detail pane */}
          <div>
            {active ? (
              <ProposalDetail proposal={active} />
            ) : (
              <p className="p-8 text-center text-muted-foreground">Select a proposal to review.</p>
            )}
          </div>
        </div>
      </AsyncBoundary>

      <ConfirmDialog
        open={confirmBulk}
        onOpenChange={setConfirmBulk}
        title={t("inbox.bulkConfirm", { count: bulk.approvable.length })}
        description={
          <div className="space-y-1">
            <p>{t("inbox.bulkExcludesDestructive")}.</p>
            <ul className="mt-2 space-y-0.5 text-xs">
              {toolSummary.map((s) => (
                <li key={s.tool} className="flex justify-between">
                  <span className="font-mono">{s.tool}</span>
                  <span>{s.count}</span>
                </li>
              ))}
            </ul>
          </div>
        }
        confirmLabel={`Approve ${bulk.approvable.length}`}
        onConfirm={runBulk}
      />
    </div>
  );
}
