"use client";
import Link from "next/link";
import { Server, Layers, Wallet, KeyRound, ShieldAlert } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/primitives";

const LINKS = [
  { href: "/admin/ai-gateway/providers", title: "Provider catalog", description: "LLM provider/deployments, live circuit + health status, drain control.", icon: Server },
  { href: "/admin/ai-gateway/ladders", title: "Routing ladders", description: "Per request-class model routing rungs (chat, sql-gen, judge, embed).", icon: Layers },
  { href: "/admin/ai-gateway/budgets", title: "Budgets & spend", description: "ai-gateway's own LLM-spend budgets and live spend — distinct from platform usage budgets.", icon: Wallet },
  { href: "/admin/ai-gateway/keys", title: "Virtual keys", description: "Scoped API keys agents use to call the gateway. Issue, revoke, rotate.", icon: KeyRound },
  { href: "/admin/ai-gateway/guardrails", title: "Guardrail policy", description: "PII redaction, prompt-injection classification, output-schema validation.", icon: ShieldAlert },
];

export default function AiGatewayAdminPage() {
  return (
    <div>
      <PageHeader title="AI gateway" description="LLM gateway admin plane: providers, routing, spend, keys, guardrails." />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {LINKS.map(({ href, title, description, icon: Icon }) => (
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
    </div>
  );
}
