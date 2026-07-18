"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  BarChart3,
  Bot,
  Check,
  ChevronDown,
  Cpu,
  Database,
  MessageSquareText,
  Network,
  ShieldCheck,
  Sparkles,
  Workflow,
} from "lucide-react";
import { WindroseLogo } from "@/components/brand/WindroseLogo";
import { Button } from "@/components/ui/button";

/* Demo request destination. Opens the visitor's mail client — swap the address
 * for your real sales/demo inbox (or point at a demo-request form route). */
const DEMO_HREF = "mailto:hello@windrose.ai?subject=Windrose%20AI%20%E2%80%94%20demo%20request";

/* ------------------------------------------------------------------ */
/* scroll-reveal (dependency-free)                                     */
/* ------------------------------------------------------------------ */
function Reveal({
  children,
  className = "",
  delay = 0,
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { threshold: 0.12 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      style={{ transitionDelay: `${delay}ms` }}
      className={`wr-reveal ${shown ? "wr-in" : ""} ${className}`}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the AI-capability tabs (the centerpiece)                            */
/* ------------------------------------------------------------------ */
const CAPS = [
  {
    key: "agents",
    icon: Bot,
    eyebrow: "Agentic workforce",
    title: "A team of specialist AI agents",
    body:
      "Purpose-built agents do the reading and draft the work — triaging cases, answering data questions, designing dashboards, training models, watching for drift. They propose; they never act on their own.",
    points: ["Draft dispositions with cited evidence", "Route work to the right specialist", "Open proposals, never silent writes"],
  },
  {
    key: "copilot",
    icon: MessageSquareText,
    eyebrow: "Conversational",
    title: "A copilot that knows your role",
    body:
      "Ask in plain language and get an answer grounded in your governed data and your permissions. Need a change made? The copilot proposes it for a human to approve — it can't do anything you couldn't.",
    points: ["Grounded in your metrics, not guesses", "Aware of what you're allowed to see", "Turns a question into a governed action"],
  },
  {
    key: "entity",
    icon: Network,
    eyebrow: "Data unification",
    title: "Entity resolution",
    body:
      "The same person or party shows up across systems under different identifiers. Windrose unifies those fragmented records into one resolved entity — so decisions run on the full picture, not a single row.",
    points: ["Deterministic + probabilistic matching", "Ambiguous merges reviewed by a human", "Decide on total exposure, not one record"],
  },
  {
    key: "decisions",
    icon: Workflow,
    eyebrow: "Codified policy",
    title: "No-code decision automation",
    body:
      "Turn your operating policy into decision tables anyone can read. Apply them consistently across thousands of cases — and every table change is itself reviewed before it goes live.",
    points: ["Author rules without engineering", "Consistent outcomes, every case", "Change control on the logic itself"],
  },
  {
    key: "analytics",
    icon: BarChart3,
    eyebrow: "Governed insight",
    title: "Analytics your team can trust",
    body:
      "Business metrics are defined once, reviewed, and reused everywhere — so dashboards agree. Click any bar, slice or row and the whole board filters to match.",
    points: ["One trusted definition per metric", "Cross-filter the entire board", "From a chart straight into a work queue"],
  },
  {
    key: "ml",
    icon: Cpu,
    eyebrow: "Own your models",
    title: "Machine learning, built in",
    body:
      "Train candidate models on your own decisions, evaluate them against your benchmarks, and promote the winner through an approval gate. Repetitive calls move off expensive AI and onto models you own.",
    points: ["No-code pipelines over a rich algorithm catalog", "Evaluated before anything ships", "Cost per decision trends down"],
  },
] as const;

/* the real specialist agents, shown as a moving roster */
const AGENTS = [
  "Case Triage",
  "Analytics",
  "Governance",
  "Onboarding",
  "Dashboard Designer",
  "Model Training",
  "ML Engineer",
  "Inference",
  "Meta Router",
];

const STEPS = [
  ["AI does the reading", "Agents gather the evidence, check it against your rules, and draft a clear recommendation with the reasoning laid out."],
  ["Your expert decides", "Approve, adjust or override. People stay accountable for every outcome — nothing acts on its own."],
  ["It learns and improves", "Each decision becomes training data. Quality climbs, the routine gets automated, and your team is freed for the hard calls."],
];

const SOLUTIONS = [
  ["Insurance Claims", "Resolve denials, appeals and prior authorizations faster — with the reasoning attached to every call."],
  ["Provider Revenue Cycle", "Lift clean-claim rates, work denials down and recover the revenue you've earned."],
  ["Fraud, Waste & Abuse", "Surface suspect claims and providers, then run each investigation to a defensible close."],
  ["Care Management", "Enroll, track and bill chronic-care and remote-monitoring programs without leaving revenue behind."],
  ["Pharmacy Benefits", "Speed authorization turnaround while protecting patient safety and rebate capture."],
  ["Post-Acute Care", "Run episodes and assessments cleanly and stay ahead of readmissions."],
  ["Financial Crime / AML", "Monitor transactions, screen for sanctions and reach filing decisions you can stand behind."],
  ["...and your operation next", "New solutions install onto the same platform — your teams learn the tool once and reuse it everywhere."],
] as const;

const TRUST = [
  ["Your data stays yours", "Cleanly isolated for your organization — never mingled, never shared."],
  ["Least-privilege access", "Everyone sees and does exactly what their role allows, and nothing more."],
  ["A second set of eyes", "The changes that matter most require another reviewer to sign off before they go live."],
  ["A tamper-evident trail", "Who decided what, when, and on what evidence — captured for every action, ready for any review."],
] as const;

const FAQ = [
  ["Does the AI ever act on its own?", "No. Agents draft recommendations and the copilot proposes changes, but a person approves, adjusts or rejects every outcome. Sensitive changes need a second reviewer too."],
  ["How is this different from a BI tool or a chatbot?", "Windrose is built around the decision, not the dashboard or the chat window. It reads the evidence, drafts the call, records who decided and why, and learns from every correction — end to end, under governance."],
  ["Will it work with our existing data and stack?", "Bring data as files or from your sources; it's profiled on arrival and queryable quickly. Metrics and models are defined on top, so you keep your system of record."],
  ["How do we get started?", "Begin from a solution shaped for your domain — the data model, metrics, work queues and expertise already in place — instead of a blank slate."],
  ["How do you keep AI costs from spiraling?", "Work is routed across model tiers, and repetitive decisions migrate onto models you own — so scaling volume doesn't mean scaling the bill."],
];

/* ------------------------------------------------------------------ */
/* small illustrative product mocks (divs, not screenshots)            */
/* ------------------------------------------------------------------ */
function Dot({ className = "" }: { className?: string }) {
  return <span className={`inline-block size-1.5 rounded-full ${className}`} />;
}

function HeroMock() {
  return (
    <div className="wr-float relative w-full max-w-md">
      <div className="absolute -inset-4 -z-10 rounded-[2rem] bg-primary/20 blur-2xl" />
      <div className="rounded-2xl border border-border/70 bg-card/95 p-5 shadow-2xl backdrop-blur">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Bot className="size-4 text-primary" />
            Case Triage agent
          </div>
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
            Proposal
          </span>
        </div>
        <div className="mt-4">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Recommended disposition</div>
          <div className="mt-1 text-base font-semibold">Deny — duplicate submission</div>
        </div>
        <div className="mt-3">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span>Confidence</span>
            <span className="font-medium text-foreground">High</span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div className="wr-grow h-full rounded-full bg-primary" />
          </div>
        </div>
        <div className="mt-4 space-y-1.5">
          {["Matches a prior claim on policy + invoice", "Same claimant and amount as the earlier submission"].map((e) => (
            <div key={e} className="flex items-start gap-2 text-xs text-muted-foreground">
              <Check className="mt-0.5 size-3.5 shrink-0 text-primary" />
              {e}
            </div>
          ))}
        </div>
        <div className="mt-5 flex items-center gap-2">
          <div className="flex-1 rounded-md bg-primary px-3 py-2 text-center text-xs font-semibold text-primary-foreground">
            Approve
          </div>
          <div className="flex-1 rounded-md border border-border px-3 py-2 text-center text-xs font-semibold text-foreground">
            Adjust
          </div>
        </div>
        <div className="mt-3 flex items-center gap-1.5 text-[10px] text-muted-foreground">
          <ShieldCheck className="size-3" />
          Logged with evidence · you decide
        </div>
      </div>
    </div>
  );
}

function CapVisual({ k }: { k: string }) {
  if (k === "agents")
    return (
      <div className="grid grid-cols-2 gap-2">
        {["Triage", "Analytics", "ML Engineer", "Governance"].map((a, i) => (
          <div key={a} className="flex items-center gap-2 rounded-lg border border-border/70 bg-background/70 p-3 text-xs">
            <span className="flex size-6 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Bot className="size-3.5" />
            </span>
            <span className="font-medium">{a}</span>
            <Dot className={`ml-auto ${i % 2 ? "bg-primary" : "bg-emerald-500"} wr-pulse`} />
          </div>
        ))}
      </div>
    );
  if (k === "copilot")
    return (
      <div className="space-y-2">
        <div className="ml-auto w-4/5 rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-xs text-primary-foreground">
          Which denials spiked this week, and why?
        </div>
        <div className="w-11/12 rounded-2xl rounded-bl-sm border border-border/70 bg-background/70 px-3 py-2 text-xs text-muted-foreground">
          Timely-filing denials rose on two payers. <span className="font-medium text-foreground">Draft a rule to auto-flag them?</span>
        </div>
        <div className="flex gap-2">
          <span className="rounded-md bg-primary/10 px-2 py-1 text-[10px] font-semibold text-primary">Propose rule</span>
          <span className="rounded-md border border-border px-2 py-1 text-[10px] text-muted-foreground">Open worklist</span>
        </div>
      </div>
    );
  if (k === "entity")
    return (
      <div className="flex items-center justify-between gap-2">
        <div className="space-y-1.5">
          {["V. Petrov · sys A", "Viktor P. · sys B", "Petrov, V · sys C"].map((r) => (
            <div key={r} className="rounded-md border border-border/70 bg-background/70 px-2.5 py-1.5 text-[11px] text-muted-foreground">
              {r}
            </div>
          ))}
        </div>
        <ArrowRight className="size-4 shrink-0 text-primary" />
        <div className="rounded-xl border border-primary/40 bg-primary/5 px-3 py-3 text-center">
          <Network className="mx-auto size-5 text-primary" />
          <div className="mt-1 text-xs font-semibold">One resolved entity</div>
          <div className="text-[10px] text-muted-foreground">full exposure</div>
        </div>
      </div>
    );
  if (k === "decisions")
    return (
      <div className="space-y-1.5 font-mono text-[11px]">
        {[
          ["IF", "exposure ≥ threshold", "→ escalate"],
          ["IF", "duplicate = true", "→ deny"],
          ["ELSE", "", "→ standard review"],
        ].map(([a, b, c], i) => (
          <div key={i} className="flex items-center gap-2 rounded-md border border-border/70 bg-background/70 px-2.5 py-1.5">
            <span className="font-semibold text-primary">{a}</span>
            <span className="text-muted-foreground">{b}</span>
            <span className="ml-auto font-medium text-foreground">{c}</span>
          </div>
        ))}
      </div>
    );
  if (k === "analytics")
    return (
      <div>
        <div className="flex items-end gap-1.5">
          {[45, 70, 40, 90, 60, 80].map((h, i) => (
            <div key={i} className="flex-1 rounded-t bg-primary/70" style={{ height: `${h}px` }} />
          ))}
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2">
          {["Denial rate", "Clean-claim", "A/R days"].map((t) => (
            <div key={t} className="rounded-md border border-border/70 bg-background/70 px-2 py-1.5 text-center text-[10px] text-muted-foreground">
              {t}
            </div>
          ))}
        </div>
      </div>
    );
  // ml
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      {["Train", "Evaluate", "Promote"].map((s, i) => (
        <div key={s} className="flex items-center gap-1.5">
          <span className={`rounded-md border px-2.5 py-1.5 font-medium ${i === 2 ? "border-primary/40 bg-primary/10 text-primary" : "border-border/70 bg-background/70 text-muted-foreground"}`}>
            {s}
          </span>
          {i < 2 && <ArrowRight className="size-3 text-muted-foreground" />}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* page                                                                */
/* ------------------------------------------------------------------ */
export default function WelcomeContent() {
  const [tab, setTab] = useState(0);
  const [auto, setAuto] = useState(true);
  const [faq, setFaq] = useState<number | null>(0);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => setTab((v) => (v + 1) % CAPS.length), 4200);
    return () => clearInterval(t);
  }, [auto]);

  const Cap = CAPS[tab];

  return (
    <main id="main" className="min-h-screen bg-background text-foreground">
      <style>{WR_CSS}</style>

      {/* header */}
      <header className="sticky top-0 z-30 border-b border-border/60 bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2.5">
            <WindroseLogo className="size-8" />
            <span className="text-lg font-bold tracking-tight">Windrose AI</span>
          </div>
          <nav className="hidden items-center gap-7 text-sm text-muted-foreground md:flex">
            <a href="#capabilities" className="transition-colors hover:text-foreground">Capabilities</a>
            <a href="#how" className="transition-colors hover:text-foreground">How it works</a>
            <a href="#solutions" className="transition-colors hover:text-foreground">Solutions</a>
            <a href="#faq" className="transition-colors hover:text-foreground">FAQ</a>
          </nav>
          <div className="flex items-center gap-4">
            <Link
              href="/login"
              className="hidden text-sm font-medium text-muted-foreground transition-colors hover:text-foreground sm:block"
            >
              Sign in
            </Link>
            <Button asChild>
              <a href={DEMO_HREF}>Request a demo</a>
            </Button>
          </div>
        </div>
      </header>

      {/* hero */}
      <section className="relative overflow-hidden">
        <div aria-hidden className="wr-mesh pointer-events-none absolute inset-0 -z-10" />
        <div className="mx-auto grid max-w-6xl items-center gap-12 px-6 pb-20 pt-14 md:grid-cols-2 md:pt-20">
          <div>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-primary">
              <Sparkles className="size-3.5" />
              AI decision intelligence
            </span>
            <h1 className="mt-6 text-balance text-4xl font-bold leading-[1.05] tracking-tight md:text-6xl">
              AI agents that decide,
              <br />
              with your experts{" "}
              <span className="wr-grad bg-clip-text text-transparent">in command.</span>
            </h1>
            <p className="mt-6 max-w-xl text-pretty text-lg leading-relaxed text-muted-foreground">
              Windrose AI puts a team of specialist agents to work on your highest-stakes
              decisions — claims, authorizations, alerts, investigations. Agents draft, a copilot
              assists, your people decide, and every correction trains the next model.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-3">
              <Button asChild size="lg">
                <a href={DEMO_HREF}>
                  Request a demo <ArrowRight className="size-4" />
                </a>
              </Button>
              <Button asChild size="lg" variant="outline">
                <a href="#capabilities">See the capabilities</a>
              </Button>
            </div>
            <p className="mt-6 text-sm text-muted-foreground">
              Governed end to end — every determination has evidence, an owner, and a trail.
            </p>
          </div>
          <div className="flex justify-center md:justify-end">
            <HeroMock />
          </div>
        </div>

        {/* moving agent roster */}
        <div className="border-y border-border/60 bg-card/40 py-4">
          <div className="mx-auto max-w-6xl overflow-hidden px-6">
            <div className="flex items-center gap-3">
              <span className="shrink-0 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
                Your AI team
              </span>
              <div className="wr-marquee-wrap flex-1">
                <div className="wr-marquee flex gap-2">
                  {[...AGENTS, ...AGENTS].map((a, i) => (
                    <span
                      key={i}
                      className="flex shrink-0 items-center gap-1.5 rounded-full border border-border/70 bg-background px-3 py-1.5 text-xs text-muted-foreground"
                    >
                      <Bot className="size-3.5 text-primary" />
                      {a}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* who it's for */}
      <section className="border-b border-border/60 bg-card/50">
        <div className="mx-auto max-w-5xl px-6 py-10">
          <p className="text-center text-pretty text-base leading-relaxed text-muted-foreground">
            Built for{" "}
            <span className="font-medium text-foreground">regulated, decision-heavy operations</span>{" "}
            — health payers and providers, pharmacy benefit managers, post-acute networks and
            financial-crime teams. If your analysts make hundreds of judgment calls a day, and your
            auditors ask how each one was made, Windrose AI was built for you.
          </p>
        </div>
      </section>

      {/* capabilities showcase (interactive tabs) */}
      <section id="capabilities" className="mx-auto max-w-6xl scroll-mt-20 px-6 py-20">
        <Reveal>
          <h2 className="text-balance text-3xl font-bold tracking-tight md:text-4xl">
            One platform. A whole AI operation.
          </h2>
          <p className="mt-3 max-w-2xl text-muted-foreground">
            Not a single model bolted onto a workflow — a coordinated set of AI capabilities that
            read, reason, decide and learn, with governance running through all of it.
          </p>
        </Reveal>

        <div className="mt-10 grid gap-8 lg:grid-cols-[1.1fr_1fr]">
          {/* tab list */}
          <div className="grid gap-2.5 sm:grid-cols-2">
            {CAPS.map((c, i) => {
              const Icon = c.icon;
              const active = i === tab;
              return (
                <button
                  key={c.key}
                  onMouseEnter={() => {
                    setAuto(false);
                    setTab(i);
                  }}
                  onClick={() => {
                    setAuto(false);
                    setTab(i);
                  }}
                  className={`group rounded-xl border p-4 text-left transition-all ${
                    active
                      ? "border-primary/50 bg-primary/5 shadow-sm"
                      : "border-border/70 bg-card hover:border-primary/30"
                  }`}
                >
                  <span
                    className={`flex size-9 items-center justify-center rounded-lg transition-colors ${
                      active ? "bg-primary text-primary-foreground" : "bg-primary/10 text-primary"
                    }`}
                  >
                    <Icon className="size-5" />
                  </span>
                  <div className="mt-3 text-sm font-semibold">{c.title}</div>
                  <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{c.eyebrow}</div>
                </button>
              );
            })}
          </div>

          {/* active panel */}
          <div className="relative overflow-hidden rounded-2xl border border-border/70 bg-gradient-to-b from-card to-card/40 p-7">
            <div aria-hidden className="pointer-events-none absolute -right-16 -top-16 size-48 rounded-full bg-primary/10 blur-2xl" />
            <div key={Cap.key} className="wr-swap relative">
              <div className="text-xs font-semibold uppercase tracking-widest text-primary">{Cap.eyebrow}</div>
              <h3 className="mt-2 text-xl font-bold tracking-tight">{Cap.title}</h3>
              <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{Cap.body}</p>
              <ul className="mt-4 space-y-1.5">
                {Cap.points.map((p) => (
                  <li key={p} className="flex items-start gap-2 text-sm">
                    <Check className="mt-0.5 size-4 shrink-0 text-primary" />
                    <span className="text-muted-foreground">{p}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-6 rounded-xl border border-border/60 bg-background/60 p-4">
                <CapVisual k={Cap.key} />
              </div>
            </div>
          </div>
        </div>

        {/* progress dots */}
        <div className="mt-6 flex items-center justify-center gap-1.5">
          {CAPS.map((c, i) => (
            <button
              key={c.key}
              aria-label={c.title}
              onClick={() => {
                setAuto(false);
                setTab(i);
              }}
              className={`h-1.5 rounded-full transition-all ${i === tab ? "w-6 bg-primary" : "w-1.5 bg-border"}`}
            />
          ))}
        </div>
      </section>

      {/* how it works */}
      <section id="how" className="border-t border-border/60 bg-card/50">
        <div className="mx-auto max-w-6xl scroll-mt-20 px-6 py-20">
          <Reveal>
            <h2 className="text-3xl font-bold tracking-tight">How every decision flows</h2>
            <p className="mt-3 max-w-2xl text-muted-foreground">
              Three steps, every time — so the work moves quickly and the accountability never leaves your people.
            </p>
          </Reveal>
          <div className="mt-10 grid gap-6 md:grid-cols-3">
            {STEPS.map(([title, body], i) => (
              <Reveal key={title} delay={i * 90}>
                <div className="relative h-full rounded-2xl border border-border/70 bg-background p-7">
                  <div className="flex size-9 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
                    {i + 1}
                  </div>
                  <h3 className="mt-4 text-lg font-semibold">{title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{body}</p>
                </div>
              </Reveal>
            ))}
          </div>
          <div className="mt-10 flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="text-sm font-medium text-foreground">What changes for your team:</span>
            {["Shorter backlogs", "Consistent determinations", "Confident audits", "Lower cost per decision", "Experts on judgment, not busywork"].map((o) => (
              <span key={o} className="rounded-full border border-border/70 bg-card px-3 py-1 text-xs text-muted-foreground">
                {o}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* solutions */}
      <section id="solutions" className="mx-auto max-w-6xl scroll-mt-20 px-6 py-20">
        <Reveal>
          <h2 className="text-3xl font-bold tracking-tight">Built for your domain, ready to run</h2>
          <p className="mt-3 max-w-2xl text-muted-foreground">
            Start from a solution shaped for your operation — the data, the metrics, the work queues
            and the domain expertise already in place — instead of a blank slate.
          </p>
        </Reveal>
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {SOLUTIONS.map(([name, body], i) => (
            <Reveal key={name} delay={(i % 4) * 70}>
              <div className="group h-full rounded-2xl border border-border/70 bg-card p-5 transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-sm">
                <div className="flex items-center gap-2">
                  <Database className="size-4 text-primary" />
                  <h3 className="text-sm font-semibold">{name}</h3>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* trust */}
      <section className="border-t border-border/60 bg-card/50">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <Reveal>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-primary">
              <ShieldCheck className="size-3.5" />
              Built for scrutiny
            </span>
            <h2 className="mt-5 text-3xl font-bold tracking-tight">Governance isn't a feature. It's the foundation.</h2>
            <p className="mt-3 max-w-2xl text-muted-foreground">
              The controls a regulated buyer needs are how the whole thing works — so security and
              compliance are on your side from day one.
            </p>
          </Reveal>
          <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {TRUST.map(([title, body], i) => (
              <Reveal key={title} delay={i * 70}>
                <div>
                  <h3 className="text-sm font-semibold">{title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{body}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* faq */}
      <section id="faq" className="mx-auto max-w-3xl scroll-mt-20 px-6 py-20">
        <h2 className="text-center text-3xl font-bold tracking-tight">Questions, answered</h2>
        <div className="mt-8 divide-y divide-border/60 rounded-2xl border border-border/70 bg-card">
          {FAQ.map(([q, a], i) => {
            const open = faq === i;
            return (
              <div key={q}>
                <button
                  onClick={() => setFaq(open ? null : i)}
                  className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
                >
                  <span className="text-sm font-semibold">{q}</span>
                  <ChevronDown className={`size-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
                </button>
                <div className={`grid transition-all duration-300 ${open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
                  <div className="overflow-hidden">
                    <p className="px-5 pb-4 text-sm leading-relaxed text-muted-foreground">{a}</p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* closing CTA */}
      <section className="relative overflow-hidden border-t border-border/60">
        <div aria-hidden className="wr-mesh pointer-events-none absolute inset-0 -z-10 opacity-70" />
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-5 px-6 py-24 text-center">
          <WindroseLogo className="size-12" />
          <h2 className="text-balance text-3xl font-bold tracking-tight md:text-4xl">
            Many directions. One confident, auditable bearing.
          </h2>
          <p className="max-w-xl text-pretty text-muted-foreground">
            Put an AI operation to work on the calls that matter — with your experts in command and a
            record that speaks for itself when anyone asks.
          </p>
          <Button asChild size="lg" className="mt-2">
            <a href={DEMO_HREF}>
              Request a demo <ArrowRight className="size-4" />
            </a>
          </Button>
          <Link href="/login" className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground">
            Already a customer? Sign in
          </Link>
        </div>
      </section>

      <footer className="border-t border-border/60">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-2 px-6 py-6 text-xs text-muted-foreground sm:flex-row">
          <span>Windrose AI — Decision Intelligence platform</span>
          <span>AI proposes. People decide. The platform remembers.</span>
        </div>
      </footer>
    </main>
  );
}

/* keyframes + reveal, injected globally (unique wr- prefixes avoid collisions) */
const WR_CSS = `
.wr-reveal{opacity:0;transform:translateY(16px);transition:opacity .6s ease,transform .6s ease;}
.wr-in{opacity:1;transform:none;}
.wr-float{animation:wr-float 6s ease-in-out infinite;}
@keyframes wr-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}
.wr-pulse{animation:wr-pulse 1.8s ease-in-out infinite;}
@keyframes wr-pulse{0%,100%{opacity:1}50%{opacity:.35}}
.wr-grow{width:0;animation:wr-grow 1.4s .3s cubic-bezier(.2,.8,.2,1) forwards;}
@keyframes wr-grow{to{width:82%}}
.wr-swap{animation:wr-swap .5s ease;}
@keyframes wr-swap{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.wr-marquee-wrap{overflow:hidden;-webkit-mask-image:linear-gradient(90deg,transparent,#000 8%,#000 92%,transparent);mask-image:linear-gradient(90deg,transparent,#000 8%,#000 92%,transparent);}
.wr-marquee{width:max-content;animation:wr-marquee 26s linear infinite;}
@keyframes wr-marquee{to{transform:translateX(-50%)}}
.wr-mesh{background:
  radial-gradient(40rem 40rem at 15% -10%, hsl(var(--primary) / 0.14), transparent 60%),
  radial-gradient(30rem 30rem at 95% 0%, hsl(var(--primary) / 0.10), transparent 55%);}
.wr-grad{background-image:linear-gradient(90deg,hsl(var(--primary)),hsl(var(--primary) / 0.55));}
@media (prefers-reduced-motion: reduce){
  .wr-float,.wr-pulse,.wr-grow,.wr-marquee,.wr-swap{animation:none!important;}
  .wr-reveal{opacity:1!important;transform:none!important;}
  .wr-grow{width:82%;}
}
`;
