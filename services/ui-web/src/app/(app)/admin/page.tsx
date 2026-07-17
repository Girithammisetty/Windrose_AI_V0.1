"use client";
import Link from "next/link";
import { Users, UsersRound, Boxes, Building2, KeyRound, KeySquare, ScrollText, Archive, Wallet, ShieldCheck, Siren, Brain, Router, Wrench, BellRing, ArrowLeftRight } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/primitives";

// Admin surfaces grouped into sections so the 15-item index reads as three
// coherent areas (access, AI, operations) instead of a flat card wall.
const SECTIONS = [
  {
    title: "Access & identity",
    links: [
      { href: "/admin/users", title: "Users", description: "Invite, deactivate, and role summary per user.", icon: Users },
      { href: "/admin/groups", title: "Groups", description: "Group CRUD, permission matrix, content grants.", icon: ShieldCheck },
      { href: "/admin/teams", title: "Teams", description: "Team CRUD and membership.", icon: UsersRound },
      // Tier 4b: identity/rbac admin — custom-role CRUD (system roles immutable).
      { href: "/admin/roles", title: "Roles", description: "Custom role CRUD and action sets; system roles are immutable.", icon: KeySquare },
      { href: "/admin/workspaces", title: "Workspaces", description: "Workspace CRUD, custom fields, member roles.", icon: Boxes },
      { href: "/admin/service-accounts", title: "Service accounts", description: "Machine principals (identity-service).", icon: KeyRound },
    ],
  },
  {
    title: "AI & agents",
    links: [
      { href: "/admin/agents", title: "Agents & kill switches", description: "Agent catalog, per-tenant agent config, and emergency stop for a live agent or tool.", icon: Siren },
      { href: "/admin/tools", title: "Tool registry", description: "Tool catalog lifecycle, tenant enablement, and BYO onboarding.", icon: Wrench },
      { href: "/admin/ai-gateway", title: "AI gateway", description: "LLM provider catalog, routing ladders, spend budgets, virtual keys, guardrails.", icon: Router },
      { href: "/admin/memory", title: "Agent memory", description: "Browse agent memory and process right-to-be-forgotten requests.", icon: Brain },
    ],
  },
  {
    title: "Operations",
    links: [
      { href: "/admin/usage", title: "Usage & budgets", description: "AI cost panel, budgets, and rate card.", icon: Wallet },
      { href: "/admin/notifications", title: "Notification settings", description: "Subscription rules, webhooks, templates, and delivery health.", icon: BellRing },
      { href: "/admin/writebacks", title: "Decision write-backs", description: "Governed sync of platform decisions to a tenant's system of record.", icon: ArrowLeftRight },
      { href: "/admin/audit", title: "Audit search", description: "Search the audit log with dual-attribution.", icon: ScrollText },
      { href: "/admin/tenant", title: "Tenant settings", description: "Tenant profile, provisioning status, isolation tier.", icon: Building2 },
      { href: "/admin/archive", title: "Archive", description: "Soft-deleted resources and restore.", icon: Archive },
    ],
  },
];

export default function AdminHomePage() {
  return (
    <div>
      <PageHeader title="Administration" description="Access management, tenant settings, usage, and audit." />
      <div className="space-y-8">
        {SECTIONS.map((section) => (
          <section key={section.title}>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
              {section.title}
            </h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {section.links.map(({ href, title, description, icon: Icon }) => (
                <Link key={href} href={href} className="focus-visible:outline-none">
                  <Card className="h-full transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-primary">
                    <CardHeader>
                      <div className="mb-1 text-muted-foreground">
                        <Icon className="size-5" aria-hidden />
                      </div>
                      <CardTitle className="text-base">{title}</CardTitle>
                      <CardDescription>{description}</CardDescription>
                    </CardHeader>
                  </Card>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
