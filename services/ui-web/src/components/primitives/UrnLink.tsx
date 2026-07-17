"use client";
import Link from "next/link";
import { useState } from "react";
import { Copy, Check, Database, FileBarChart, Briefcase, Boxes, Bot } from "lucide-react";
import { cn, urnParts } from "@/lib/utils";

const ICONS: Record<string, typeof Database> = {
  dataset: Database,
  chart: FileBarChart,
  dashboard: FileBarChart,
  case: Briefcase,
  model: Boxes,
  run: Bot,
  proposal: Bot,
  agent_run: Bot,
};

/** Route a URN to its detail screen. */
function hrefFor(type?: string, path?: string): string | null {
  if (!type || !path) return null;
  const id = path.split("/").pop();
  switch (type) {
    case "dataset":
      return `/data/datasets/${id}`;
    case "case":
      return `/cases/${id}`;
    case "dashboard":
      return `/dashboards/${id}`;
    case "run":
    case "agent_run":
      return `/copilot/runs/${id}`;
    case "model":
      return `/ml/models`;
    default:
      return null;
  }
}

/**
 * Renders any Windrose URN as a typed deep link with an icon + copy action
 * (UI-FR-019). Unknown types render as inert text with copy.
 */
export function UrnLink({ urn, label, className }: { urn: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const { type, path } = urnParts(urn);
  const Icon = (type && ICONS[type]) || Boxes;
  const href = hrefFor(type, path);
  const text = label ?? path?.split("/").pop() ?? urn;

  const inner = (
    <span className="inline-flex items-center gap-1">
      <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="truncate">{text}</span>
    </span>
  );

  return (
    <span className={cn("group inline-flex max-w-full items-center gap-1", className)}>
      {href ? (
        <Link href={href} className="truncate text-primary hover:underline">
          {inner}
        </Link>
      ) : (
        <span className="truncate text-foreground">{inner}</span>
      )}
      <button
        type="button"
        aria-label={`Copy ${urn}`}
        title={urn}
        onClick={() => {
          void navigator.clipboard?.writeText(urn);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        }}
        className="opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
      >
        {copied ? <Check className="size-3 text-[hsl(var(--success))]" /> : <Copy className="size-3 text-muted-foreground" />}
      </button>
    </span>
  );
}
