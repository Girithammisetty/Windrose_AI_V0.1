"use client";
import { useState } from "react";
import { Check, X, Pencil, MessageCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { AiLabel } from "@/components/primitives/AiLabel";
import { DiffView } from "@/components/primitives/DiffView";
import { UrnLink } from "@/components/primitives/UrnLink";
import { StatusChip } from "@/components/primitives/StatusChip";
import { useDecideProposal } from "@/lib/graphql/hooks";
import { useToasts } from "@/stores/ui";
import { normalizeArgsDiff } from "@/lib/diff";
import { isDestructiveTool } from "@/lib/agentic/proposals";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { t } from "@/lib/i18n/messages";
import { toolLabel, agentLabel } from "@/lib/labels";
import type { Proposal } from "@/lib/graphql/types";

type Mode = "view" | "reject" | "edit" | "respond";

/**
 * Proposal detail + decision actions (UI-FR-033). Renders rationale, predicted
 * effect, affected URNs, and the args DiffView. Reject requires a reason (AC-6);
 * edit-args seeds a form with the proposed args and highlights the edited diff;
 * respond asks the agent for clarification. Concurrent-decision conflicts resolve
 * to the final state instead of a raw error (BR-4).
 */
export function ProposalDetail({ proposal }: { proposal: Proposal }) {
  const decide = useDecideProposal();
  const push = useToasts((s) => s.push);
  const [mode, setMode] = useState<Mode>("view");
  const [reason, setReason] = useState("");
  const [response, setResponse] = useState("");
  const [editedText, setEditedText] = useState(() =>
    JSON.stringify(normalizeArgsDiff(proposal.argsDiff).after, null, 2),
  );
  const destructive = isDestructiveTool(proposal.tool);

  function onDone(label: string) {
    push({ title: label, variant: "success" });
    setMode("view");
    setReason("");
    setResponse("");
  }

  function onErr(err: unknown) {
    const g = err instanceof GraphQLRequestError ? err : null;
    if (g?.code === "CONFLICT") {
      // BR-4: already decided elsewhere — resolve softly, no raw error.
      push({ title: "Already decided by someone else", variant: "default", traceId: g.traceId });
      setMode("view");
      return;
    }
    push({ title: "Decision failed", description: g?.message, traceId: g?.traceId, variant: "error" });
  }

  const approve = () =>
    decide.mutate(
      { id: proposal.id, decision: { kind: "APPROVE" } },
      { onSuccess: () => onDone("Approved"), onError: onErr },
    );

  const reject = () => {
    if (!reason.trim()) return; // AC-6: reason mandatory
    decide.mutate(
      { id: proposal.id, decision: { kind: "REJECT", reason } },
      { onSuccess: () => onDone("Rejected"), onError: onErr },
    );
  };

  const submitEdit = () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(editedText);
    } catch {
      push({ title: "Edited args are not valid JSON", variant: "error" });
      return;
    }
    decide.mutate(
      { id: proposal.id, decision: { kind: "EDIT_ARGS", editedArgs: parsed } },
      { onSuccess: () => onDone("Approved with edits"), onError: onErr },
    );
  };

  const respond = () => {
    if (!response.trim()) return;
    decide.mutate(
      { id: proposal.id, decision: { kind: "RESPOND", responseText: response } },
      { onSuccess: () => onDone("Sent to agent"), onError: onErr },
    );
  };

  // Preview diff for edit mode: proposed → edited.
  const editedDiff = (() => {
    try {
      return { before: normalizeArgsDiff(proposal.argsDiff).after, after: JSON.parse(editedText) };
    } catch {
      return null;
    }
  })();

  return (
    <Card data-proposal-detail={proposal.id}>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <AiLabel />
        <CardTitle className="text-base">{toolLabel(proposal.tool)}</CardTitle>
        <StatusChip status={proposal.status} className="ml-1" />
        {destructive && (
          <span className="rounded-full bg-destructive/15 px-2 py-0.5 text-xs font-medium text-destructive">
            destructive
          </span>
        )}
        {proposal.agentKey && <span className="ml-auto text-xs text-muted-foreground">{agentLabel(proposal.agentKey)}</span>}
      </CardHeader>
      <CardContent className="space-y-4">
        {proposal.rationale && (
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Rationale</h3>
            <p className="text-sm">{proposal.rationale}</p>
          </section>
        )}
        {proposal.predictedEffect && (
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Predicted effect</h3>
            <p className="text-sm">{proposal.predictedEffect.summary}</p>
            {(proposal.predictedEffect.reversibility || proposal.predictedEffect.blast_radius != null) && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {proposal.predictedEffect.reversibility}
                {proposal.predictedEffect.reversibility && proposal.predictedEffect.blast_radius != null && " · "}
                {proposal.predictedEffect.blast_radius != null &&
                  `blast radius ${proposal.predictedEffect.blast_radius}`}
              </p>
            )}
          </section>
        )}
        {proposal.affectedUrns.length > 0 && (
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Affected resources</h3>
            <div className="flex flex-col gap-1">
              {proposal.affectedUrns.map((u) => (
                <UrnLink key={u} urn={u} />
              ))}
            </div>
          </section>
        )}

        <section>
          <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Proposed args</h3>
          <DiffView argsDiff={proposal.argsDiff} />
        </section>

        {mode === "view" && (
          <div className="flex flex-wrap gap-2">
            <Button onClick={approve} disabled={decide.isPending} data-testid="approve">
              <Check className="size-4" /> {t("action.approve")}
            </Button>
            <Button variant="destructive" onClick={() => setMode("reject")}>
              <X className="size-4" /> {t("action.reject")}
            </Button>
            <Button variant="outline" onClick={() => setMode("edit")}>
              <Pencil className="size-4" /> {t("action.editArgs")}
            </Button>
            <Button variant="ghost" onClick={() => setMode("respond")}>
              <MessageCircle className="size-4" /> {t("action.respond")}
            </Button>
          </div>
        )}

        {mode === "reject" && (
          <div className="space-y-2">
            <label className="text-sm font-medium">Reason (required)</label>
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("inbox.rejectReasonRequired")}
              aria-label="Rejection reason"
              data-testid="reject-reason"
            />
            <div className="flex gap-2">
              <Button variant="destructive" onClick={reject} disabled={!reason.trim() || decide.isPending}>
                Confirm reject
              </Button>
              <Button variant="ghost" onClick={() => setMode("view")}>
                {t("action.cancel")}
              </Button>
            </div>
          </div>
        )}

        {mode === "edit" && (
          <div className="space-y-2">
            <label className="text-sm font-medium">Edit proposed args</label>
            <Textarea
              value={editedText}
              onChange={(e) => setEditedText(e.target.value)}
              className="min-h-[140px] font-mono text-xs"
              aria-label="Edited args"
            />
            {editedDiff && (
              <div>
                <p className="mb-1 text-xs text-muted-foreground">Your edits vs. the proposal:</p>
                <DiffView argsDiff={editedDiff} />
              </div>
            )}
            <div className="flex gap-2">
              <Button onClick={submitEdit} disabled={decide.isPending}>
                Approve with edits
              </Button>
              <Button variant="ghost" onClick={() => setMode("view")}>
                {t("action.cancel")}
              </Button>
            </div>
          </div>
        )}

        {mode === "respond" && (
          <div className="space-y-2">
            <label className="text-sm font-medium">Ask the agent</label>
            <Textarea value={response} onChange={(e) => setResponse(e.target.value)} aria-label="Response to agent" />
            <div className="flex gap-2">
              <Button onClick={respond} disabled={!response.trim() || decide.isPending}>
                Send
              </Button>
              <Button variant="ghost" onClick={() => setMode("view")}>
                {t("action.cancel")}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
