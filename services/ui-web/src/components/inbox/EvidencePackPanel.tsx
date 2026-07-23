"use client";
import { useState } from "react";
import { ShieldCheck, ShieldAlert, Download, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useEvidencePack } from "@/lib/graphql/hooks";
import type { EvidencePack } from "@/lib/graphql/operations";

/**
 * BRD 60 WS5 — the auditor evidence pack for one decision, on the decision
 * detail. Lazily fetches the tamper-evident pack (four-eyes summary + every
 * WORM event's chain position + per-day verification) and offers a JSON
 * download. The pack IS the evidence: an examiner can recompute the chain from
 * the events and confirm against the referenced sealed manifest.
 */
export function EvidencePackPanel({ proposalId }: { proposalId: string }) {
  const [open, setOpen] = useState(false);
  const q = useEvidencePack(proposalId, open);

  function download(pack: EvidencePack) {
    const blob = new Blob([JSON.stringify(pack, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `evidence-pack-${proposalId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="rounded-md border border-border/60 bg-muted/30 p-3" data-testid="evidence-pack">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase text-muted-foreground">
          <FileText className="size-3.5" /> Auditor evidence pack
        </h3>
        {!open && (
          <Button variant="outline" size="sm" onClick={() => setOpen(true)} data-testid="evidence-pack-view">
            View evidence pack
          </Button>
        )}
      </div>

      {open && q.isPending && <p className="mt-2 text-sm text-muted-foreground">Assembling from the WORM chain…</p>}
      {open && q.isError && (
        <p className="mt-2 text-sm text-destructive">
          Could not load the evidence pack (it may not have any recorded events yet).
        </p>
      )}

      {open && q.data && <PackBody pack={q.data} onDownload={() => download(q.data!)} />}
    </section>
  );
}

function PackBody({ pack, onDownload }: { pack: EvidencePack; onDownload: () => void }) {
  const d = pack.decision;
  const allSealedValid =
    pack.chainProof.length > 0 && pack.chainProof.every((p) => p.sealed && p.valid && p.manifestMatch);
  return (
    <div className="mt-3 space-y-3 text-sm">
      {/* the four-eyes claim, made prominent */}
      <div
        className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs font-medium ${
          d.fourEyes
            ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
            : "bg-amber-500/10 text-amber-700 dark:text-amber-400"
        }`}
        data-testid="evidence-four-eyes"
      >
        {d.fourEyes ? <ShieldCheck className="size-4" /> : <ShieldAlert className="size-4" />}
        {d.fourEyes
          ? `Four-eyes: a distinct human (${d.approver}) approved`
          : d.approver
            ? "Not four-eyes: the approver is the same person the agent acted for"
            : "No distinct human approval recorded yet"}
      </div>

      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Proposed by</dt>
        <dd className="font-mono">
          {d.agentId}
          {d.agentVersion ? `@${d.agentVersion}` : ""}
          {d.onBehalfOf ? ` on behalf of ${d.onBehalfOf}` : " (autonomous)"}
        </dd>
        <dt className="text-muted-foreground">Outcome</dt>
        <dd>
          {d.outcome}
          {d.approver ? ` by ${d.approver}` : ""}
        </dd>
        {d.toolId && (
          <>
            <dt className="text-muted-foreground">Tool call</dt>
            <dd className="font-mono">
              {d.toolId}
              {d.toolVersion ? `@${d.toolVersion}` : ""}
              {d.argsDigest ? ` · args ${d.argsDigest.slice(0, 12)}…` : ""}
            </dd>
          </>
        )}
      </dl>

      {/* tamper-evidence per chain-day */}
      <div>
        <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
          Tamper-evidence
          {pack.chainProof.length > 0 && (
            <span
              className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${
                allSealedValid ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-muted"
              }`}
            >
              {allSealedValid ? "verified" : "pending seal"}
            </span>
          )}
        </p>
        <ul className="space-y-1 text-xs">
          {pack.chainProof.map((p) => (
            <li key={p.chainDate} className="flex items-center gap-2">
              <span className="font-mono text-muted-foreground">{p.chainDate}</span>
              {p.sealed ? (
                <span className={p.valid && p.manifestMatch ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}>
                  {p.valid && p.manifestMatch
                    ? `✓ chain re-verified (${p.eventsChecked} events) against the sealed WORM manifest`
                    : "✗ chain mismatch — integrity alert"}
                </span>
              ) : (
                <span className="text-muted-foreground">{p.note || "not sealed yet"}</span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <p className="text-[11px] leading-snug text-muted-foreground">{pack.integrity}</p>

      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onDownload} data-testid="evidence-pack-download">
          <Download className="size-4" /> Download JSON ({pack.events.length} events)
        </Button>
      </div>
    </div>
  );
}
