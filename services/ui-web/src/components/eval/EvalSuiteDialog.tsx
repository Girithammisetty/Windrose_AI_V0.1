"use client";
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCreateEvalSuite, useUpdateEvalSuite } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { CreateEvalSuiteInput, EvalSuite, UpdateEvalSuiteInput } from "@/lib/graphql/types";
import { JsonField, formatJsonText, jsonError } from "./JsonField";

const DATASETS_PLACEHOLDER = `[
  { "datasetKey": "claims-agent/nl2sql", "version": 1 }
]`;
const SCORERS_PLACEHOLDER = `[
  { "scorerKey": "exact-match", "version": 1, "weight": 1 }
]`;
const JUDGE_LADDER_PLACEHOLDER = `{
  "requestClass": "judge",
  "maxRung": 2
}`;

/**
 * Author an eval suite (eval-service POST/PATCH /suites). Structured fields
 * (datasets, scorers, judge-ladder pin) are JSON textareas with parse-on-blur
 * validation + a Format button; scalar fields are plain inputs. Reused in two
 * modes: create (blank) and edit (prefilled from `editSuite`; suiteId + agentKey
 * are immutable and submit commits via updateEvalSuite).
 */
export function EvalSuiteDialog({
  open,
  onOpenChange,
  defaultAgentKey,
  editSuite,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  defaultAgentKey?: string;
  editSuite?: EvalSuite | null;
}) {
  const create = useCreateEvalSuite();
  const update = useUpdateEvalSuite();
  const isEdit = !!editSuite;
  const active = isEdit ? update : create;

  const [suiteId, setSuiteId] = useState("");
  const [agentKey, setAgentKey] = useState("");
  const [gateRule, setGateRule] = useState("");
  const [baselineVersion, setBaselineVersion] = useState("");
  const [minCases, setMinCases] = useState("");
  const [datasetsText, setDatasetsText] = useState("");
  const [scorersText, setScorersText] = useState("");
  const [judgeLadderText, setJudgeLadderText] = useState("");

  const [datasetsErr, setDatasetsErr] = useState<string | null>(null);
  const [scorersErr, setScorersErr] = useState<string | null>(null);
  const [judgeLadderErr, setJudgeLadderErr] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    create.reset();
    update.reset();
    setSuiteId(editSuite?.suiteId ?? "");
    setAgentKey(editSuite?.agentKey ?? defaultAgentKey ?? "");
    setGateRule(editSuite?.gateRule ?? "");
    setBaselineVersion(editSuite?.baselineVersion ?? "");
    setMinCases(editSuite?.minCases != null ? String(editSuite.minCases) : "");
    setDatasetsText(editSuite?.datasets != null ? JSON.stringify(editSuite.datasets, null, 2) : "");
    setScorersText(editSuite?.scorers != null ? JSON.stringify(editSuite.scorers, null, 2) : "");
    setJudgeLadderText(editSuite?.judgeLadderPin != null ? JSON.stringify(editSuite.judgeLadderPin, null, 2) : "");
    setDatasetsErr(null);
    setScorersErr(null);
    setJudgeLadderErr(null);
    setBanner(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only on open
  }, [open]);

  const submit = () => {
    setBanner(null);
    if (!suiteId.trim() || !agentKey.trim() || !gateRule.trim()) {
      setBanner("Suite id, agent key, and gate rule are required.");
      return;
    }
    const dErr = jsonError(datasetsText, { required: true });
    const sErr = jsonError(scorersText, { required: true });
    const jErr = jsonError(judgeLadderText);
    setDatasetsErr(dErr);
    setScorersErr(sErr);
    setJudgeLadderErr(jErr);
    if (dErr || sErr || jErr) return;

    if (minCases.trim() && Number.isNaN(Number(minCases))) {
      setBanner("Min cases must be a number.");
      return;
    }

    if (isEdit) {
      const input: UpdateEvalSuiteInput = {
        suiteId: editSuite!.suiteId,
        version: editSuite!.version,
        gateRule: gateRule.trim(),
        datasets: JSON.parse(datasetsText),
        scorers: JSON.parse(scorersText),
        baselineVersion: baselineVersion.trim() || undefined,
        minCases: minCases.trim() ? Number(minCases) : undefined,
        judgeLadderPin: judgeLadderText.trim() ? JSON.parse(judgeLadderText) : undefined,
      };
      update.mutate(input, {
        onSuccess: () => onOpenChange(false),
        onError: (e) =>
          setBanner(e instanceof GraphQLRequestError ? e.message : "Could not update suite."),
      });
      return;
    }

    const input: CreateEvalSuiteInput = {
      suiteId: suiteId.trim(),
      agentKey: agentKey.trim(),
      gateRule: gateRule.trim(),
      datasets: JSON.parse(datasetsText),
      scorers: JSON.parse(scorersText),
      baselineVersion: baselineVersion.trim() || undefined,
      minCases: minCases.trim() ? Number(minCases) : undefined,
      judgeLadderPin: judgeLadderText.trim() ? JSON.parse(judgeLadderText) : undefined,
    };
    create.mutate(input, {
      onSuccess: () => onOpenChange(false),
      onError: (e) =>
        setBanner(e instanceof GraphQLRequestError ? e.message : "Could not create suite."),
    });
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[92vw] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-background p-5 shadow-lg">
          <Dialog.Title className="text-lg font-semibold">{isEdit ? "Edit eval suite" : "New eval suite"}</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-muted-foreground">
            A suite pins the datasets and scorers a scoring run evaluates, plus the gate rule
            that decides pass/fail.
          </Dialog.Description>

          <div className="mt-4 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <Label htmlFor="suite-id">Suite id *</Label>
                <Input id="suite-id" value={suiteId} onChange={(e) => setSuiteId(e.target.value)} placeholder="claims-agent/nl2sql" disabled={isEdit} />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="suite-agent">Agent key *</Label>
                <Input id="suite-agent" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} placeholder="claims-agent" disabled={isEdit} />
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="suite-gate">Gate rule *</Label>
              <Input id="suite-gate" value={gateRule} onChange={(e) => setGateRule(e.target.value)} placeholder="mean(exact-match) >= 0.9" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <Label htmlFor="suite-baseline">Baseline version (optional)</Label>
                <Input id="suite-baseline" value={baselineVersion} onChange={(e) => setBaselineVersion(e.target.value)} placeholder="v3" />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="suite-min-cases">Min cases (optional)</Label>
                <Input id="suite-min-cases" type="number" min="0" value={minCases} onChange={(e) => setMinCases(e.target.value)} placeholder="20" />
              </div>
            </div>

            <JsonField
              id="suite-datasets"
              label="Datasets (JSON)"
              required
              value={datasetsText}
              onChange={setDatasetsText}
              onBlur={() => setDatasetsErr(jsonError(datasetsText, { required: true }))}
              onFormat={() => {
                const r = formatJsonText(datasetsText);
                if ("text" in r) {
                  setDatasetsText(r.text);
                  setDatasetsErr(null);
                } else setDatasetsErr(r.error);
              }}
              error={datasetsErr}
              placeholder={DATASETS_PLACEHOLDER}
            />
            <JsonField
              id="suite-scorers"
              label="Scorers (JSON)"
              required
              value={scorersText}
              onChange={setScorersText}
              onBlur={() => setScorersErr(jsonError(scorersText, { required: true }))}
              onFormat={() => {
                const r = formatJsonText(scorersText);
                if ("text" in r) {
                  setScorersText(r.text);
                  setScorersErr(null);
                } else setScorersErr(r.error);
              }}
              error={scorersErr}
              placeholder={SCORERS_PLACEHOLDER}
            />
            <JsonField
              id="suite-judge-ladder"
              label="Judge-ladder pin (JSON, optional)"
              rows={5}
              value={judgeLadderText}
              onChange={setJudgeLadderText}
              onBlur={() => setJudgeLadderErr(jsonError(judgeLadderText))}
              onFormat={() => {
                const r = formatJsonText(judgeLadderText);
                if ("text" in r) {
                  setJudgeLadderText(r.text);
                  setJudgeLadderErr(null);
                } else setJudgeLadderErr(r.error);
              }}
              error={judgeLadderErr}
              placeholder={JUDGE_LADDER_PLACEHOLDER}
            />

            {banner && (
              <p role="alert" className="text-sm text-destructive">
                {banner}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                onClick={submit}
                disabled={active.isPending || !!datasetsErr || !!scorersErr || !!judgeLadderErr}
              >
                {isEdit
                  ? (update.isPending ? "Saving…" : "Save changes")
                  : (create.isPending ? "Creating…" : "Create suite")}
              </Button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
