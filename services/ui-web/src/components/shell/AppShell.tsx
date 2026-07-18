"use client";
import { useMemo } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ToastHost } from "./ToastHost";
import { CommandPalette } from "./CommandPalette";
import { CopilotDrawer } from "@/components/copilot/CopilotDrawer";
import { SessionProvider, type SessionInfo } from "@/lib/session/SessionContext";
import { useProposalsInbox } from "@/lib/graphql/hooks";
import { useCostPanel } from "@/lib/graphql/hooks";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { cap } from "@/lib/authz/registry";
import { RouteGuard } from "@/components/authz/RouteGuard";

function todayRange(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getTime() - 30 * 86400_000);
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

function ShellInner({ children, session }: { children: React.ReactNode; session: SessionInfo }) {
  // Task #78: these were tenant/user-wide "any proposal of mine" / "any usage
  // event" subscriptions, but realtime-hub's topic grammar only routes to a
  // single resource (run-status:<urn>, proposal:<id>) — there is no broadcast
  // scheme for "all of a tenant's/user's events of a kind" yet, so every
  // subscribe here was a guaranteed 422 INVALID_TOPIC. Removed rather than left
  // silently failing; the inbox badge and cost panel below already fall back
  // to their own polling/refetch, so nothing regresses. Re-add once a
  // tenant-broadcast scheme exists (tracked as a follow-up to #78).

  // Global shell surfaces are themselves capability-gated: a persona without
  // proposal-read never fetches the inbox badge, one without usage-read never
  // fetches the cost panel — the UI never fires a call the server would 403.
  const { can } = useCapabilities();
  const canSeeInbox = can(cap("ai.proposal.read"));
  const canSeeCost = can(cap("usage.report.read"));

  const inbox = useProposalsInbox({ status: "PENDING" }, { enabled: canSeeInbox });
  const pendingCount = inbox.data?.pages?.[0]?.pageInfo
    ? inbox.data.pages.reduce((n, p) => n + p.nodes.length, 0)
    : undefined;

  const { from, to } = useMemo(todayRange, []);
  const cost = useCostPanel(session.workspaceId, from, to, { enabled: canSeeCost });
  const budgetExhausted = (cost.data?.workspaceCostPanel.budgetStates ?? []).some(
    (b) => b.exhaustedAt != null || (b.limit != null && b.consumed != null && b.consumed >= b.limit),
  );

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar pendingCount={pendingCount} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main id="main" className="flex-1 overflow-auto p-4 md:p-6" tabIndex={-1}>
          <RouteGuard>{children}</RouteGuard>
        </main>
      </div>
      <CopilotDrawer budgetExhausted={budgetExhausted} />
      <ToastHost />
      <CommandPalette />
    </div>
  );
}

export function AppShell({ children, session }: { children: React.ReactNode; session: SessionInfo }) {
  return (
    <SessionProvider value={session}>
      <ShellInner session={session}>{children}</ShellInner>
    </SessionProvider>
  );
}
