"use client";
import { useMemo, useState } from "react";
import { ListChecks, Check, X, Trash2, ShieldCheck, Pencil, Plus } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { EvalCaseDialog } from "@/components/eval/EvalCaseDialog";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useEvalCases, usePromoteEvalCase, useAttestEvalCase, useRejectEvalCase, useRetireEvalCase,
} from "@/lib/graphql/hooks";
import type { EvalCase } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

const STATUS_VARIANT: Record<string, "default" | "success" | "secondary"> = {
  candidate: "default",
  active: "success",
  retired: "secondary",
};

export default function EvalCasesPage() {
  const [datasetKey, setDatasetKey] = useState("");
  const [status, setStatus] = useState("candidate");
  const [attestingId, setAttestingId] = useState<string | null>(null);
  const [attestedBy, setAttestedBy] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<EvalCase | null>(null);

  const query = useEvalCases({ datasetKey: datasetKey || undefined, status: status || undefined });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const promote = usePromoteEvalCase();
  const attest = useAttestEvalCase();
  const reject = useRejectEvalCase();
  const retire = useRetireEvalCase();

  const columns: Column<EvalCase>[] = [
    { id: "id", header: "Case", cell: (c) => <span className="font-mono text-xs">{c.id}</span> },
    { id: "dataset", header: "Dataset", cell: (c) => `${c.datasetKey}@v${c.datasetVersion}` },
    { id: "source", header: "Source", width: 140, cell: (c) => c.source },
    { id: "status", header: "Status", width: 100, cell: (c) => <Badge variant={STATUS_VARIANT[c.status] ?? "default"}>{c.status}</Badge> },
    { id: "attested", header: "Attested", width: 100, cell: (c) => (c.anonymizationAttestedBy ? <Check className="size-4 text-[hsl(var(--success))]" /> : "—") },
    { id: "createdAt", header: "Created", width: 170, cell: (c) => formatLocal(c.createdAt) },
    {
      id: "actions", header: "", width: 300,
      cell: (c) => (
        <Can gate={FEATURE_GATES.curateEvalCase}>
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => { setEditing(c); setDialogOpen(true); }}>
              <Pencil className="size-3" /> Edit
            </Button>
            {c.status === "candidate" && (
              <>
                {!c.anonymizationAttestedBy && (
                  <Button size="sm" variant="outline" onClick={() => { setAttestingId(c.id); setAttestedBy(""); }}>
                    <ShieldCheck className="size-3" /> Attest
                  </Button>
                )}
                <Button size="sm" variant="outline" disabled={promote.isPending} onClick={() => promote.mutate(c.id)}>
                  <Check className="size-3" /> Promote
                </Button>
                <Button size="sm" variant="ghost" disabled={reject.isPending} onClick={() => reject.mutate(c.id)}>
                  <X className="size-3" /> Reject
                </Button>
              </>
            )}
            {c.status === "active" && (
              <Button size="sm" variant="ghost" disabled={retire.isPending} onClick={() => retire.mutate(c.id)}>
                <Trash2 className="size-3" /> Retire
              </Button>
            )}
          </div>
        </Can>
      ),
    },
  ];

  const newButton = (
    <Can gate={FEATURE_GATES.curateEvalCase}>
      <Button onClick={() => { setEditing(null); setDialogOpen(true); }}>
        <Plus /> New case
      </Button>
    </Can>
  );

  return (
    <div>
      <PageHeader title="Case curation queue" description="Promote candidate cases sourced from production traces, verified queries, and HITL corrections." actions={newButton} />

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-end gap-2 pt-4">
          <div className="flex flex-col gap-1">
            <Label htmlFor="case-dataset">Dataset key</Label>
            <Input id="case-dataset" value={datasetKey} onChange={(e) => setDatasetKey(e.target.value)} placeholder="claims-agent/nl2sql" className="w-64" />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="case-status">Status</Label>
            <select id="case-status" value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
              <option value="candidate">candidate</option>
              <option value="active">active</option>
              <option value="retired">retired</option>
              <option value="">any</option>
            </select>
          </div>
        </CardContent>
      </Card>

      {attestingId && (
        <Card className="mb-4 border-primary/40">
          <CardContent className="flex flex-wrap items-end gap-2 pt-4">
            <div className="flex flex-col gap-1">
              <Label htmlFor="attested-by">Attested by (reviewer id)</Label>
              <Input id="attested-by" value={attestedBy} onChange={(e) => setAttestedBy(e.target.value)} className="h-9 w-56" />
            </div>
            <Button
              disabled={!attestedBy.trim() || attest.isPending}
              onClick={() => attest.mutate({ id: attestingId, attestedBy: attestedBy.trim() }, { onSuccess: () => setAttestingId(null) })}
            >
              Confirm anonymization attestation
            </Button>
            <Button variant="outline" onClick={() => setAttestingId(null)}>Cancel</Button>
          </CardContent>
        </Card>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No cases match this filter"
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Case curation queue"
          rows={rows}
          columns={columns}
          rowId={(c) => c.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <ListChecks className="size-8" />
              <p>No cases</p>
            </div>
          }
        />
      </AsyncBoundary>

      <EvalCaseDialog
        open={dialogOpen}
        onOpenChange={(o) => { setDialogOpen(o); if (!o) setEditing(null); }}
        initial={editing}
        defaultDatasetKey={datasetKey || undefined}
      />
    </div>
  );
}
