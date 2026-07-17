"use client";
import * as Popover from "@radix-ui/react-popover";
import Link from "next/link";
import { Sparkles, ExternalLink } from "lucide-react";
import { formatLocal, nonSuppressibleClassName } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

export interface Provenance {
  agent?: string;
  agentKey?: string;
  version?: string;
  sourceRunId?: string;
  approvedBy?: string;
  approvingUser?: string;
  timestamp?: string;
  createdAt?: string;
  [k: string]: unknown;
}

/**
 * "AI-generated" provenance badge (UI-FR-032, BR-2). Renders whenever provenance
 * is non-null — in lists, detail headers, and exports. Non-suppressible: passing
 * a non-null provenance always yields a visible badge. Clicking opens details
 * with a deep link to the agent-run trace.
 */
export function ProvenanceBadge({
  provenance,
  className,
}: {
  provenance?: Provenance | null;
  className?: string;
}) {
  if (provenance == null) return null;
  const runId = provenance.sourceRunId ?? (provenance as any).runId;
  const agent = provenance.agent ?? provenance.agentKey ?? "agent";
  const when = provenance.timestamp ?? provenance.createdAt;
  const approver = provenance.approvedBy ?? provenance.approvingUser;

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          data-provenance-badge="true"
          className={nonSuppressibleClassName(
            "inline-flex items-center gap-1 rounded-full border border-ai/40 bg-ai/10 px-2 py-0.5 text-xs font-medium text-ai hover:bg-ai/20 focus-visible:outline-none",
            className,
          )}
          aria-label={t("ai.generated")}
        >
          <Sparkles className="size-3" aria-hidden />
          {t("ai.generated")}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          className="z-50 w-72 rounded-md border bg-card p-3 text-card-foreground shadow-md"
        >
          <p className="mb-2 text-sm font-semibold">{t("ai.provenance.title")}</p>
          <dl className="space-y-1 text-xs">
            <Row label="Agent" value={agent} />
            {provenance.version && <Row label="Version" value={String(provenance.version)} />}
            {approver && <Row label="Approved by" value={String(approver)} />}
            {when && <Row label="When" value={formatLocal(String(when))} />}
          </dl>
          {runId && (
            <Link
              href={`/copilot/runs/${runId}`}
              className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              View agent-run trace <ExternalLink className="size-3" aria-hidden />
            </Link>
          )}
          <Popover.Arrow className="fill-border" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium">{value}</dd>
    </div>
  );
}
