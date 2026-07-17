"use client";
import { useMemo, useState } from "react";
import { Plus, Gauge, Zap } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useEvalScorers, useCreateEvalScorer, useUpdateEvalScorer, useActivateEvalScorer } from "@/lib/graphql/hooks";
import type { EvalScorer, UpdateEvalScorerInput } from "@/lib/graphql/types";

const STATUS_VARIANT: Record<string, "default" | "success" | "secondary"> = {
  draft: "default",
  active: "success",
  retired: "secondary",
};

export default function EvalScorersPage() {
  const [creating, setCreating] = useState(false);
  // Edit-in-place: the same scorer form reused against an existing scorer.
  // scorerKey + kind are immutable; the rest commit via updateEvalScorer.
  const [editing, setEditing] = useState<EvalScorer | null>(null);
  const query = useEvalScorers();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const create = useCreateEvalScorer();
  const update = useUpdateEvalScorer();
  const activate = useActivateEvalScorer();

  const columns: Column<EvalScorer>[] = [
    { id: "key", header: "Scorer key", cell: (s) => <span className="font-mono text-xs">{s.scorerKey}</span> },
    { id: "version", header: "Version", width: 90, cell: (s) => `v${s.version}` },
    { id: "kind", header: "Kind", width: 130, cell: (s) => s.kind },
    { id: "gateEligible", header: "Gate-eligible", width: 110, cell: (s) => (s.gateEligible ? "yes" : "no") },
    {
      id: "agreement", header: "Judge agreement", width: 130,
      cell: (s) => (s.judgeAgreement != null ? s.judgeAgreement.toFixed(2) : "—"),
    },
    { id: "status", header: "Status", width: 100, cell: (s) => <Badge variant={STATUS_VARIANT[s.status] ?? "default"}>{s.status}</Badge> },
    {
      id: "actions", header: "", width: 170,
      cell: (s) => (
        <Can gate={FEATURE_GATES.manageEvalScorers}>
          <div className="flex gap-1">
            <Button
              size="sm" variant="ghost"
              onClick={() => { setCreating(false); setEditing(s); }}
            >
              Edit
            </Button>
            {s.status === "draft" && (
              <Button
                size="sm" variant="outline" disabled={activate.isPending}
                onClick={() => activate.mutate({ scorerKey: s.scorerKey, version: s.version })}
              >
                <Zap className="size-3" /> Activate
              </Button>
            )}
          </div>
        </Can>
      ),
    },
  ];

  const newButton = (
    <Can gate={FEATURE_GATES.manageEvalScorers}>
      <Button onClick={() => { setEditing(null); setCreating((v) => !v); }}><Plus /> {creating ? "Cancel" : "Register scorer"}</Button>
    </Can>
  );

  return (
    <div>
      <PageHeader
        title="Scorers"
        description="Deterministic + LLM-judge scorers. Judge activation is blocked below 0.8 judge-vs-human agreement."
        actions={newButton}
      />

      {(creating || editing) && (
        <Card className="mb-4 border-primary/40">
          <CardContent className="pt-4">
            <NewScorerForm
              key={editing?.id ?? "new"}
              editScorer={editing}
              pending={editing ? update.isPending : create.isPending}
              error={editing ? update.error : create.error}
              onCreate={(input) => create.mutate(input, { onSuccess: () => setCreating(false) })}
              onSave={(input) => update.mutate(input, { onSuccess: () => setEditing(null) })}
              onCancel={() => { setCreating(false); setEditing(null); }}
            />
          </CardContent>
        </Card>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No scorers registered"
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Scorers"
          rows={rows}
          columns={columns}
          rowId={(s) => s.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Gauge className="size-8" />
              <p>No scorers</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}

const SCORER_STATUSES = ["draft", "active", "retired"];

/**
 * The single scorer authoring form, reused for register (blank) and edit
 * (prefilled from `editScorer`). In edit mode scorerKey, version and kind are
 * immutable (read-only) and submit commits the mutable fields via
 * updateEvalScorer; otherwise the minimal register call runs.
 */
function NewScorerForm({
  onCreate,
  onSave,
  onCancel,
  editScorer,
  pending,
  error,
}: {
  onCreate: (input: { scorerKey: string; version: number; kind: string }) => void;
  onSave: (input: UpdateEvalScorerInput) => void;
  onCancel: () => void;
  editScorer: EvalScorer | null;
  pending: boolean;
  error: Error | null;
}) {
  const isEdit = !!editScorer;
  const [scorerKey, setScorerKey] = useState(editScorer?.scorerKey ?? "");
  const [version, setVersion] = useState(String(editScorer?.version ?? 1));
  const [kind, setKind] = useState(editScorer?.kind ?? "deterministic");
  // Mutable-in-edit fields, prefilled from the scorer.
  const [gateEligible, setGateEligible] = useState(!!editScorer?.gateEligible);
  const [status, setStatus] = useState(editScorer?.status ?? "draft");
  const [judgeAgreement, setJudgeAgreement] = useState(editScorer?.judgeAgreement != null ? String(editScorer.judgeAgreement) : "");
  const [imageRef, setImageRef] = useState(editScorer?.imageRef ?? "");
  const [judgePromptRef, setJudgePromptRef] = useState(editScorer?.judgePromptRef ?? "");
  const [judgePromptVer, setJudgePromptVer] = useState(editScorer?.judgePromptVer ?? "");
  const [applicableKinds, setApplicableKinds] = useState((editScorer?.applicableExpectedKinds ?? []).join(", "));
  const [configSchemaText, setConfigSchemaText] = useState(
    editScorer?.configSchema != null ? JSON.stringify(editScorer.configSchema, null, 2) : "",
  );
  const [localErr, setLocalErr] = useState<string | null>(null);

  const submit = () => {
    if (!isEdit) {
      if (scorerKey.trim() && version.trim()) onCreate({ scorerKey: scorerKey.trim(), version: Number(version), kind });
      return;
    }
    let configSchema: unknown;
    if (configSchemaText.trim()) {
      try {
        configSchema = JSON.parse(configSchemaText);
      } catch {
        setLocalErr("Config schema must be valid JSON.");
        return;
      }
    }
    setLocalErr(null);
    onSave({
      scorerKey: editScorer!.scorerKey,
      version: editScorer!.version,
      gateEligible,
      status,
      judgeAgreement: judgeAgreement.trim() ? Number(judgeAgreement) : undefined,
      imageRef: imageRef.trim() || undefined,
      judgePromptRef: judgePromptRef.trim() || undefined,
      judgePromptVer: judgePromptVer.trim() || undefined,
      applicableExpectedKinds: applicableKinds.trim()
        ? applicableKinds.split(",").map((s) => s.trim()).filter(Boolean)
        : undefined,
      configSchema,
    });
  };

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => { e.preventDefault(); submit(); }}
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="new-scorer-key">Scorer key</Label>
        <Input id="new-scorer-key" value={scorerKey} onChange={(e) => setScorerKey(e.target.value)} placeholder="exact_match" className="h-9 w-48" disabled={isEdit} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="new-scorer-version">Version</Label>
        <Input id="new-scorer-version" type="number" min="1" value={version} onChange={(e) => setVersion(e.target.value)} className="h-9 w-24" disabled={isEdit} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="new-scorer-kind">Kind</Label>
        <select id="new-scorer-kind" value={kind} onChange={(e) => setKind(e.target.value)} disabled={isEdit} className="h-9 rounded-md border border-input bg-background px-2 text-sm disabled:opacity-60">
          <option value="deterministic">deterministic</option>
          <option value="llm_judge">llm_judge</option>
        </select>
      </div>

      {isEdit && (
        <>
          <div className="flex flex-col gap-1">
            <Label htmlFor="scorer-status">Status</Label>
            <select id="scorer-status" value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
              {SCORER_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <label className="flex h-9 items-center gap-2 text-sm">
            <input type="checkbox" checked={gateEligible} onChange={(e) => setGateEligible(e.target.checked)} className="size-4 accent-[hsl(var(--primary))]" />
            Gate-eligible
          </label>
          <div className="flex flex-col gap-1">
            <Label htmlFor="scorer-agreement">Judge agreement</Label>
            <Input id="scorer-agreement" type="number" step="0.01" min="0" max="1" value={judgeAgreement} onChange={(e) => setJudgeAgreement(e.target.value)} placeholder="0.85" className="h-9 w-28" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="scorer-image">Image ref</Label>
            <Input id="scorer-image" value={imageRef} onChange={(e) => setImageRef(e.target.value)} placeholder="ghcr.io/…" className="h-9 w-48" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="scorer-prompt-ref">Judge prompt ref</Label>
            <Input id="scorer-prompt-ref" value={judgePromptRef} onChange={(e) => setJudgePromptRef(e.target.value)} className="h-9 w-40" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="scorer-prompt-ver">Judge prompt ver</Label>
            <Input id="scorer-prompt-ver" value={judgePromptVer} onChange={(e) => setJudgePromptVer(e.target.value)} className="h-9 w-32" />
          </div>
          <div className="flex w-full flex-col gap-1">
            <Label htmlFor="scorer-applicable">Applicable expected kinds (comma-separated)</Label>
            <Input id="scorer-applicable" value={applicableKinds} onChange={(e) => setApplicableKinds(e.target.value)} placeholder="sql, text" className="h-9" />
          </div>
          <div className="flex w-full flex-col gap-1">
            <Label htmlFor="scorer-config">Config schema (JSON)</Label>
            <textarea
              id="scorer-config"
              value={configSchemaText}
              onChange={(e) => setConfigSchemaText(e.target.value)}
              rows={4}
              placeholder={'{ "type": "object" }'}
              className="w-full rounded-md border border-input bg-background px-2 py-1.5 font-mono text-xs"
            />
          </div>
        </>
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>{isEdit ? (pending ? "Saving…" : "Save") : "Register"}</Button>
        {isEdit && <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>}
      </div>
      {(error || localErr) && <p className="w-full text-xs text-destructive">{localErr ?? error?.message}</p>}
    </form>
  );
}
