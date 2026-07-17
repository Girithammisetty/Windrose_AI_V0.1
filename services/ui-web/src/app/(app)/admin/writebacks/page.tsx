"use client";
import { useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Button } from "@/components/ui/button";
import {
  useWritebacks, useApproveWriteback, useRejectWriteback, useRetryWriteback,
} from "@/lib/graphql/hooks";
import { useSession } from "@/lib/session/SessionContext";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { Writeback } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

const STATUSES = ["", "pending_approval", "delivering", "delivered", "failed", "rejected"];

/**
 * Decision write-backs (INS-FR-061): a bounded ops/admin list of every
 * governed sync-to-SoR job, newest first. Every job is four-eyes — approve/
 * reject are gated below on the CALLER not being the original requester, the
 * same rule ingestion-service enforces server-side (a self-approve attempt
 * 422s there regardless), so a disabled button here reflects a real
 * constraint rather than hiding one arbitrarily.
 */
export default function AdminWritebacksPage() {
  const { userId } = useSession();
  const [status, setStatus] = useState("");
  const query = useWritebacks({ status: status || undefined });
  const rows = query.data ?? [];

  const [toDecide, setToDecide] = useState<{ wb: Writeback; action: "approve" | "reject" } | null>(null);
  const approve = useApproveWriteback();
  const reject = useRejectWriteback();
  const retry = useRetryWriteback();
  const decideMutation = toDecide?.action === "reject" ? reject : approve;
  const decideError = decideMutation.error instanceof GraphQLRequestError ? decideMutation.error : null;

  const columns: Column<Writeback>[] = [
    { id: "kind", header: "Decision", width: "1.5fr", cell: (w) => <span className="font-medium">{w.decisionKind}</span> },
    { id: "ref", header: "Ref", width: "1.5fr", cell: (w) => <span className="truncate font-mono text-xs">{w.decisionRef}</span> },
    { id: "status", header: "Status", width: 150, cell: (w) => <StatusChip status={w.status} /> },
    { id: "requestedBy", header: "Requested by", width: 160, cell: (w) => <span className="truncate text-xs">{w.requestedBy}</span> },
    {
      id: "approvedBy", header: "Approved by", width: 160,
      cell: (w) => w.approvedBy ? <span className="truncate text-xs">{w.approvedBy}</span> : <span className="text-muted-foreground">—</span>,
    },
    { id: "attempts", header: "Attempts", width: 90, cell: (w) => w.attempts },
    { id: "created", header: "Created", width: 170, cell: (w) => <span className="whitespace-nowrap">{formatLocal(w.createdAt)}</span> },
    {
      id: "actions", header: "", width: 220,
      cell: (w) => {
        const selfRequested = w.requestedBy === userId;
        if (w.status === "pending_approval") {
          return (
            <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
              <Button
                size="sm" variant="outline"
                disabled={selfRequested}
                title={selfRequested ? "Four-eyes: you cannot approve your own request" : undefined}
                onClick={() => setToDecide({ wb: w, action: "approve" })}
              >
                Approve
              </Button>
              <Button
                size="sm" variant="outline"
                disabled={selfRequested}
                title={selfRequested ? "Four-eyes: you cannot decide your own request" : undefined}
                onClick={() => setToDecide({ wb: w, action: "reject" })}
              >
                Reject
              </Button>
            </div>
          );
        }
        if (w.status === "failed") {
          return (
            <Button size="sm" variant="outline" disabled={retry.isPending} onClick={(e) => { e.stopPropagation(); retry.mutate(w.id); }}>
              {retry.isPending ? "Retrying…" : "Retry"}
            </Button>
          );
        }
        return null;
      },
    },
  ];

  const [expanded, setExpanded] = useState<Writeback | null>(null);

  return (
    <div>
      <PageHeader
        title="Decision write-backs"
        description="Governed, four-eyes sync of platform decisions to a tenant's own system of record (ingestion-service)."
      />

      <div className="mb-3 flex items-center gap-2">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Status</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Status filter"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s ? s.replaceAll("_", " ") : "all"}</option>
            ))}
          </select>
        </label>
      </div>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && rows.length === 0}
        emptyTitle="No write-backs yet."
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Write-backs"
          rows={rows}
          columns={columns}
          rowId={(w) => w.id}
          onRowActivate={(w) => setExpanded(w)}
        />
      </AsyncBoundary>

      {expanded && (
        <div className="mt-4 rounded-lg border p-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Job detail</h3>
            <Button size="sm" variant="ghost" onClick={() => setExpanded(null)}>Close</Button>
          </div>
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <DetailRow label="Connection" value={expanded.connectionId} mono />
            <DetailRow label="Target ref" value={expanded.targetRef ?? "—"} mono />
            <DetailRow label="Delivered at" value={formatLocal(expanded.deliveredAt)} />
            <DetailRow label="Last error" value={expanded.lastError ?? "—"} />
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Target</p>
              <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">{JSON.stringify(expanded.target, null, 2)}</pre>
            </div>
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Payload</p>
              <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">{JSON.stringify(expanded.payload, null, 2)}</pre>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!toDecide}
        onOpenChange={(o) => !o && setToDecide(null)}
        title={toDecide?.action === "reject" ? "Reject write-back?" : "Approve write-back?"}
        description={
          toDecide?.action === "reject"
            ? "This decision will not be synced to the tenant's system of record."
            : `This delivers "${toDecide?.wb.decisionKind}" (${toDecide?.wb.decisionRef}) to the tenant's system of record.`
        }
        confirmLabel={toDecide?.action === "reject" ? "Reject" : "Approve"}
        destructive={toDecide?.action === "reject"}
        onConfirm={() => {
          if (!toDecide) return;
          const mutate = toDecide.action === "reject" ? reject.mutate : approve.mutate;
          mutate(toDecide.wb.id, { onSuccess: () => setToDecide(null) });
        }}
      >
        {decideError && (
          <p role="alert" className="mt-2 text-xs text-destructive" data-testid="mutation-error">
            {decideError.message}
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? "truncate font-mono text-xs" : "font-medium"}>{value}</span>
    </div>
  );
}
