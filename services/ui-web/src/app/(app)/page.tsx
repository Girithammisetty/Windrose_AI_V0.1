"use client";
import Link from "next/link";
import { Database, FlaskConical, BarChart3, Briefcase, Shield, Bot, Inbox } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { CostPanel } from "@/components/usage/CostPanel";
import { useMe, useProposalsInbox } from "@/lib/graphql/hooks";
import { useSession } from "@/lib/session/SessionContext";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { cap, role, ADMIN_ROLE, type Gate } from "@/lib/authz/registry";
import { t, type MessageKey } from "@/lib/i18n/messages";

/** Home tiles mirror the nav gates: only the areas the persona can reach show. */
const TILES: { href: string; icon: typeof Database; label: MessageKey; desc: string; gate: Gate }[] = [
  { href: "/data", icon: Database, label: "nav.data", desc: "Connections, ingestions, datasets", gate: cap("dataset.dataset.list") },
  { href: "/ml", icon: FlaskConical, label: "nav.ml", desc: "Experiments, runs, models", gate: cap("experiment.experiment.read") },
  { href: "/dashboards", icon: BarChart3, label: "nav.dashboards", desc: "Charts & dashboards", gate: cap("chart.dashboard.read") },
  { href: "/cases", icon: Briefcase, label: "nav.cases", desc: "Claim triage", gate: cap("case.case.read") },
  { href: "/inbox", icon: Inbox, label: "nav.inbox", desc: "Agent proposals", gate: cap("ai.proposal.read") },
  { href: "/admin", icon: Shield, label: "nav.admin", desc: "Users, RBAC, budgets", gate: role(ADMIN_ROLE) },
];

export default function HomePage() {
  const session = useSession();
  const { can } = useCapabilities();
  const canSeeInbox = can(cap("ai.proposal.read"));
  const canSeeCost = can(cap("usage.report.read"));

  const { data: me } = useMe();
  const tenantLabel = me?.me.tenantName || session.tenantId;
  const workspaceLabel = me?.me.workspaceName || session.workspaceId;

  const inbox = useProposalsInbox({ status: "PENDING" }, { enabled: canSeeInbox });
  const pending = inbox.data?.pages.reduce((n, p) => n + p.nodes.length, 0) ?? 0;

  const tiles = TILES.filter((tile) => can(tile.gate));

  return (
    <div>
      <PageHeader title={`Welcome`} description={`${tenantLabel} · ${workspaceLabel}`} />

      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {tiles.map(({ href, icon: Icon, label, desc }) => (
            <Link key={href} href={href}>
              <Card className="h-full transition-colors hover:bg-accent/50">
                <CardHeader>
                  <Icon className="size-5 text-primary" aria-hidden />
                  <CardTitle className="text-base">{t(label)}</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">{desc}</CardContent>
              </Card>
            </Link>
          ))}
        </div>

        <div className="space-y-4">
          {canSeeInbox && (
            <Card>
              <CardHeader className="flex-row items-center gap-2">
                <Bot className="size-4 text-ai" aria-hidden />
                <CardTitle className="text-sm">Pending approvals</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold">{pending}</p>
                <Link href="/inbox" className="text-sm text-primary hover:underline">
                  Open approval inbox →
                </Link>
              </CardContent>
            </Card>
          )}
          {canSeeCost && <CostPanel workspaceId={session.workspaceId} />}
        </div>
      </div>
    </div>
  );
}
