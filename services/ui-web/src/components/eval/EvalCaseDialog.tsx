"use client";
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCreateEvalCase, useUpdateEvalCase } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { CreateEvalCaseInput, EvalCase, EvalCasePatchInput } from "@/lib/graphql/types";
import { JsonField, formatJsonText, jsonError } from "./JsonField";

const INPUT_PLACEHOLDER = `{
  "question": "How many claims were denied last quarter?"
}`;
const EXPECTED_PLACEHOLDER = `{
  "sql": "SELECT count(*) FROM claims WHERE status = 'denied'"
}`;

const splitCsv = (s: string): string[] =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/**
 * Create or edit an eval case (eval-service POST /cases, PATCH /cases/{id}).
 * When `initial` is supplied the dialog is in edit mode: only the
 * PATCH-able fields (input, expected, tags, weight, attestation) are shown and
 * an `EvalCasePatchInput` is sent; otherwise it creates a new candidate case.
 * `input`/`expected` are JSON textareas with parse-on-blur validation.
 */
export function EvalCaseDialog({
  open,
  onOpenChange,
  initial,
  defaultDatasetKey,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  initial?: EvalCase | null;
  defaultDatasetKey?: string;
}) {
  const isEdit = !!initial;
  const create = useCreateEvalCase();
  const update = useUpdateEvalCase();
  const pending = create.isPending || update.isPending;

  const [datasetKey, setDatasetKey] = useState("");
  const [agentKey, setAgentKey] = useState("");
  const [inputText, setInputText] = useState("");
  const [expectedText, setExpectedText] = useState("");
  const [source, setSource] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const [tags, setTags] = useState("");
  const [weight, setWeight] = useState("");
  const [attestedBy, setAttestedBy] = useState("");

  const [inputErr, setInputErr] = useState<string | null>(null);
  const [expectedErr, setExpectedErr] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    create.reset();
    update.reset();
    setInputErr(null);
    setExpectedErr(null);
    setBanner(null);
    if (initial) {
      setInputText(JSON.stringify(initial.input, null, 2));
      setExpectedText(JSON.stringify(initial.expected, null, 2));
      setTags(initial.tags.join(", "));
      setWeight(String(initial.weight));
      setAttestedBy(initial.anonymizationAttestedBy ?? "");
      setDatasetKey(initial.datasetKey);
      setAgentKey("");
      setSource(initial.source);
      setSourceRef(initial.sourceRef ?? "");
    } else {
      setDatasetKey(defaultDatasetKey ?? "");
      setAgentKey("");
      setInputText("");
      setExpectedText("");
      setSource("");
      setSourceRef("");
      setTags("");
      setWeight("1");
      setAttestedBy("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only on open/initial change
  }, [open, initial]);

  const submit = () => {
    setBanner(null);
    const iErr = jsonError(inputText, { required: true });
    const eErr = jsonError(expectedText, { required: true });
    setInputErr(iErr);
    setExpectedErr(eErr);
    if (iErr || eErr) return;

    if (weight.trim() && Number.isNaN(Number(weight))) {
      setBanner("Weight must be a number.");
      return;
    }

    if (isEdit && initial) {
      const patch: EvalCasePatchInput = {
        input: JSON.parse(inputText),
        expected: JSON.parse(expectedText),
        tags: splitCsv(tags),
        weight: weight.trim() ? Number(weight) : undefined,
        anonymizationAttestedBy: attestedBy.trim() || undefined,
      };
      update.mutate(
        { id: initial.id, patch },
        {
          onSuccess: () => onOpenChange(false),
          onError: (err) =>
            setBanner(err instanceof GraphQLRequestError ? err.message : "Could not update case."),
        },
      );
      return;
    }

    if (!datasetKey.trim()) {
      setBanner("Dataset key is required.");
      return;
    }
    const input: CreateEvalCaseInput = {
      datasetKey: datasetKey.trim(),
      agentKey: agentKey.trim() || undefined,
      input: JSON.parse(inputText),
      expected: JSON.parse(expectedText),
      source: source.trim() || undefined,
      sourceRef: sourceRef.trim() || undefined,
      tags: splitCsv(tags),
      weight: weight.trim() ? Number(weight) : undefined,
      anonymizationAttestedBy: attestedBy.trim() || undefined,
    };
    create.mutate(input, {
      onSuccess: () => onOpenChange(false),
      onError: (err) =>
        setBanner(err instanceof GraphQLRequestError ? err.message : "Could not create case."),
    });
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[92vw] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-background p-5 shadow-lg">
          <Dialog.Title className="text-lg font-semibold">
            {isEdit ? "Edit eval case" : "New eval case"}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-muted-foreground">
            {isEdit
              ? "Update the case input, expected output, tags, or weight."
              : "Add a candidate case to a dataset. Cases enter the curation queue for review."}
          </Dialog.Description>

          <div className="mt-4 space-y-3">
            {!isEdit && (
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <Label htmlFor="case-dataset-key">Dataset key *</Label>
                  <Input id="case-dataset-key" value={datasetKey} onChange={(e) => setDatasetKey(e.target.value)} placeholder="claims-agent/nl2sql" />
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor="case-agent-key">Agent key (optional)</Label>
                  <Input id="case-agent-key" value={agentKey} onChange={(e) => setAgentKey(e.target.value)} placeholder="claims-agent" />
                </div>
              </div>
            )}

            <JsonField
              id="case-input"
              label="Input (JSON)"
              required
              value={inputText}
              onChange={setInputText}
              onBlur={() => setInputErr(jsonError(inputText, { required: true }))}
              onFormat={() => {
                const r = formatJsonText(inputText);
                if ("text" in r) {
                  setInputText(r.text);
                  setInputErr(null);
                } else setInputErr(r.error);
              }}
              error={inputErr}
              placeholder={INPUT_PLACEHOLDER}
            />
            <JsonField
              id="case-expected"
              label="Expected (JSON)"
              required
              value={expectedText}
              onChange={setExpectedText}
              onBlur={() => setExpectedErr(jsonError(expectedText, { required: true }))}
              onFormat={() => {
                const r = formatJsonText(expectedText);
                if ("text" in r) {
                  setExpectedText(r.text);
                  setExpectedErr(null);
                } else setExpectedErr(r.error);
              }}
              error={expectedErr}
              placeholder={EXPECTED_PLACEHOLDER}
            />

            {!isEdit && (
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <Label htmlFor="case-source">Source (optional)</Label>
                  <Input id="case-source" value={source} onChange={(e) => setSource(e.target.value)} placeholder="verified_query" />
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor="case-source-ref">Source ref (optional)</Label>
                  <Input id="case-source-ref" value={sourceRef} onChange={(e) => setSourceRef(e.target.value)} placeholder="urn / trace id" />
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <Label htmlFor="case-tags">Tags (comma-separated)</Label>
                <Input id="case-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="regression, edge-case" />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="case-weight">Weight</Label>
                <Input id="case-weight" type="number" step="0.1" min="0" value={weight} onChange={(e) => setWeight(e.target.value)} placeholder="1" />
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="case-attested">Anonymization attested by (optional)</Label>
              <Input id="case-attested" value={attestedBy} onChange={(e) => setAttestedBy(e.target.value)} placeholder="reviewer id" />
            </div>

            {banner && (
              <p role="alert" className="text-sm text-destructive">
                {banner}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button onClick={submit} disabled={pending || !!inputErr || !!expectedErr}>
                {pending ? "Saving…" : isEdit ? "Save changes" : "Create case"}
              </Button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
