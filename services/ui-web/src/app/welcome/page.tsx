import Link from "next/link";
import { WindroseLogo } from "@/components/brand/WindroseLogo";
import { Button } from "@/components/ui/button";

/**
 * Public pre-login marketing page (the front door for signed-out visitors).
 * Every capability and solution named here is a REAL, shipped platform
 * feature — this page is grounded in what the product does, not aspiration.
 */
export const metadata = {
  title: "Windrose AI — Decision Intelligence for regulated operations",
  description:
    "A governed, continuously learning decision platform: AI proposes, your experts decide, and every correction trains the next model — with cost, audit and access control built in.",
};

const DIFFERENTIATORS = [
  {
    title: "Human-in-the-loop by design",
    body:
      "AI never acts alone. Agents draft proposals; your experts approve, reject or correct them. Sensitive publishes — semantic models, verified queries, model promotions, external referrals — require a second reviewer (four-eyes).",
  },
  {
    title: "It learns from every decision",
    body:
      "Dispositions and corrections are first-class training data. The platform retrains candidate models on them, evaluates against your own benchmarks, and promotes only through a governed approval gate — so accuracy compounds and unit cost falls over time.",
  },
  {
    title: "AI cost under control",
    body:
      "A multi-provider AI gateway routes work across model tiers (fast-small → balanced → frontier) with per-tenant keys, budgets and per-model pricing telemetry. Repetitive decisions migrate from LLM tokens to trained models you own.",
  },
  {
    title: "Governance is the platform, not a feature",
    body:
      "Row-level tenant isolation, self-service roles and workspaces, a WORM audit trail with chain verification, agent kill switches, memory erasure, and policy-based authorization on every single action.",
  },
];

const CAPABILITIES = [
  ["Work queues & dispositions", "Triage cases with AI-suggested outcomes, evidence citations, SLAs, bulk actions and typed disposition taxonomies."],
  ["Dashboards & cross-filtering", "KPI dashboards on governed semantic models — click any bar, slice or row to filter the rest of the board."],
  ["Role-grounded copilot", "A conversational copilot that knows your role, your permissions and your domain's grounding corpus — and proposes, never executes."],
  ["Self-serve data onboarding", "Upload CSV, JSON, Parquet, Avro or XML; schemas are profiled on arrival and datasets are queryable in minutes."],
  ["Semantic models & verified queries", "Business metrics defined once, reviewed four-eyes, and compiled to safe, parameterized SQL — no raw table access."],
  ["ML lifecycle", "No-code pipelines over a 21-algorithm catalog, experiment tracking, evaluation suites and governed model promotion."],
  ["Notifications & approvals", "A real approval inbox for everything pending your judgment, with webhooks and scheduled report subscriptions."],
  ["Tenant self-service", "Admins define roles from friendly presets, invite users, manage workspaces and set AI budgets — no vendor ticket."],
] as const;

const SOLUTIONS = [
  ["Insurance Claims (Payer)", "Denials, appeals and prior-auth operations with CARC-grounded triage."],
  ["Care Management (Medicare)", "CCM/BHI/RPM/TCM enrollment, time-tracking compliance and revenue-leakage review."],
  ["Provider Revenue Cycle", "Clean-claim rates, denial analytics, A/R worklists and underpayment recovery."],
  ["Payer FWA / SIU", "Fraud, waste & abuse detection with provider peer analytics and investigation queues."],
  ["Pharmacy Benefits", "PA turnaround, DUR safety review, GDR and rebate-capture analytics."],
  ["Post-Acute Care", "PDGM/PDPM episodes, OASIS/MDS assessment ops and readmission analytics."],
  ["Banking AML", "Transaction monitoring, sanctions screening and SAR-discipline casework."],
  ["Investigation Framework", "A shared investigation methodology — chain of custody, tipping-off discipline, two-signature referrals — reused by investigation-heavy solutions."],
] as const;

export default function WelcomePage() {
  return (
    <main id="main" className="min-h-screen bg-background text-foreground">
      {/* header */}
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2.5">
          <WindroseLogo className="size-8" />
          <span className="text-lg font-bold tracking-tight">Windrose AI</span>
        </div>
        <Button asChild>
          <Link href="/login">Sign in</Link>
        </Button>
      </header>

      {/* hero */}
      <section className="mx-auto max-w-6xl px-6 pb-16 pt-12 md:pt-20">
        <div className="max-w-3xl">
          <p className="mb-3 text-sm font-semibold uppercase tracking-widest text-primary">
            Decision Intelligence platform
          </p>
          <h1 className="text-balance text-4xl font-bold leading-tight tracking-tight md:text-5xl">
            Decide with evidence.
            <br />
            Improve with every decision.
          </h1>
          <p className="mt-5 max-w-2xl text-pretty text-lg text-muted-foreground">
            Windrose AI turns high-volume, high-stakes operational decisions — claims,
            authorizations, investigations, alerts — into a governed system where AI does the
            reading, your experts make the call, and every correction trains the next model.
            Accuracy compounds. AI cost goes down, not up.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button asChild size="lg">
              <Link href="/login">Sign in to your workspace</Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <a href="#solutions">See solutions</a>
            </Button>
          </div>
        </div>
      </section>

      {/* who needs it */}
      <section className="border-y bg-card/50">
        <div className="mx-auto max-w-6xl px-6 py-10">
          <p className="mx-auto max-w-3xl text-center text-pretty text-base text-muted-foreground">
            Built for <span className="font-medium text-foreground">regulated, decision-heavy operations</span> —
            health payers and providers, pharmacy benefit managers, post-acute networks, and
            financial-crime teams — where every determination needs evidence, an audit trail,
            and a human accountable for it. If your analysts make hundreds of judgment calls a
            day and your auditors ask how each one was made, Windrose AI is for you.
          </p>
        </div>
      </section>

      {/* differentiators */}
      <section className="mx-auto max-w-6xl px-6 py-16">
        <h2 className="text-2xl font-bold tracking-tight">Why Windrose AI</h2>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          Plenty of tools bolt AI onto workflows. Windrose AI is built around the decision
          itself — proposed by AI, made by people, audited forever, and learned from.
        </p>
        <div className="mt-8 grid gap-6 md:grid-cols-2">
          {DIFFERENTIATORS.map((d) => (
            <div key={d.title} className="rounded-lg border bg-card p-6">
              <h3 className="font-semibold">{d.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{d.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* end-user capabilities */}
      <section className="border-t bg-card/50">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <h2 className="text-2xl font-bold tracking-tight">What your teams can do</h2>
          <div className="mt-8 grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-4">
            {CAPABILITIES.map(([title, body]) => (
              <div key={title}>
                <h3 className="text-sm font-semibold">{title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* solutions */}
      <section id="solutions" className="mx-auto max-w-6xl scroll-mt-6 px-6 py-16">
        <h2 className="text-2xl font-bold tracking-tight">Solutions, installable as packs</h2>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          A vertical solution — datasets, metrics, dashboards, work queues, roles, agent
          personas and regulatory grounding — installs into your tenant as a pack, on the same
          frozen platform core. Live today:
        </p>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {SOLUTIONS.map(([name, body]) => (
            <div key={name} className="rounded-lg border bg-card p-5">
              <h3 className="text-sm font-semibold">{name}</h3>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* closing CTA */}
      <section className="border-t">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-4 px-6 py-14 text-center">
          <WindroseLogo className="size-12" />
          <h2 className="text-balance text-2xl font-bold tracking-tight">
            Many directions. One confident, auditable bearing.
          </h2>
          <Button asChild size="lg" className="mt-2">
            <Link href="/login">Sign in</Link>
          </Button>
        </div>
      </section>

      <footer className="border-t">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6 text-xs text-muted-foreground">
          <span>Windrose AI — Decision Intelligence platform</span>
          <span>AI proposes. People decide. The platform remembers.</span>
        </div>
      </footer>
    </main>
  );
}
