"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FlaskConical, Database, ListChecks, Gauge, GitCompareArrows, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardHeader, CardTitle, CardDescription, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";

const SECTIONS = [
  { href: "/ml/eval/runs", title: "Runs", description: "Scoring run history; open a run for its case results, suite pins, and gate status.", icon: FlaskConical },
  { href: "/ml/eval/trends", title: "Trends / scorecard", description: "Score-trend series per scorer, across agent versions — the model-version scorecard.", icon: GitCompareArrows },
  { href: "/ml/eval/datasets", title: "Datasets", description: "Eval dataset versions per agent; freeze a version once it has active cases.", icon: Database },
  { href: "/ml/eval/cases", title: "Case queue", description: "Curate candidate cases sourced from verified queries, traces, and HITL corrections.", icon: ListChecks },
  { href: "/ml/eval/scorers", title: "Scorers", description: "The scorer registry — deterministic and LLM-judge scorers, activation gate.", icon: Gauge },
  { href: "/ml/eval/canaries", title: "Canaries", description: "Online A/B comparisons between a candidate and baseline version.", icon: ShieldCheck },
];

export default function EvalHomePage() {
  const router = useRouter();
  const [agentKey, setAgentKey] = useState("");

  return (
    <div>
      <PageHeader
        title="Eval"
        description="Model quality gates, scoring runs, and the eval flywheel (eval-service)."
      />

      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm">Jump to an agent&apos;s runs</CardTitle>
          <CardDescription>Most eval views are scoped to one agent key.</CardDescription>
        </CardHeader>
        <form
          className="flex flex-wrap items-end gap-2 px-6 pb-6"
          onSubmit={(e) => {
            e.preventDefault();
            if (agentKey.trim()) router.push(`/ml/eval/runs?agentKey=${encodeURIComponent(agentKey.trim())}`);
          }}
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor="agent-key">Agent key</Label>
            <Input id="agent-key" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} placeholder="claims-agent" className="w-64" />
          </div>
          <Button type="submit" disabled={!agentKey.trim()}>View runs</Button>
        </form>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SECTIONS.map(({ href, title, description, icon: Icon }) => (
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
