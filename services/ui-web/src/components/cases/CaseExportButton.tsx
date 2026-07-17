"use client";
import { useState } from "react";
import { Download } from "lucide-react";
import { Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useExportCases, useCaseOperation } from "@/lib/graphql/hooks";
import { useToasts } from "@/stores/ui";
import { GraphQLRequestError } from "@/lib/graphql/client";

/**
 * Async CSV export for the case list (case-service POST /cases/export,
 * CASE-FR-044). Kicks off the real export operation, polls caseOperation every
 * 2s while running (same idiom as AuditComplianceCard), then links the gzipped
 * CSV through the authed same-origin proxy /api/case-export/{id} (the
 * downstream download route needs the Bearer JWT a plain <a href> can't carry).
 *
 * Only the `status` filter is passed: case-service's export worker honours
 * exactly that one key (handlers_bulk.go statusesFromFilter) — sending q/
 * severity would silently over-promise a filtered export.
 */
export function CaseExportButton({ status }: { status?: string }) {
  const push = useToasts((s) => s.push);
  const exportCases = useExportCases();
  const [operationId, setOperationId] = useState<string | null>(null);
  const operation = useCaseOperation(operationId, {
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  // The mutation result is the operation's real initial state; the poll takes
  // over from there. Prefer the freshest read.
  const op = operation.data ?? exportCases.data ?? null;

  const run = () =>
    exportCases.mutate(
      // The export worker reads lowercase domain statuses (in_progress, …).
      { filter: status ? { status: status.toLowerCase() } : {}, format: "csv" },
      {
        onSuccess: (r) => setOperationId(r.id),
        onError: (err) => {
          const g = err instanceof GraphQLRequestError ? err : null;
          push({
            title: "Export failed to start",
            description: g?.message ?? String(err),
            traceId: g?.traceId,
            variant: "error",
          });
        },
      },
    );

  return (
    <Can gate={FEATURE_GATES.exportCases}>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={exportCases.isPending || op?.status === "running"}
          onClick={run}
        >
          <Download className="mr-1 size-3.5" aria-hidden />
          {op?.status === "running" ? "Exporting…" : "Export CSV"}
        </Button>
        {op && (
          <span className="flex items-center gap-2 text-xs">
            <Badge
              variant={op.status === "succeeded" ? "success" : op.status === "failed" ? "destructive" : "warning"}
            >
              {op.status}
            </Badge>
            {op.status === "succeeded" && (
              <a
                href={`/api/case-export/${op.id}`}
                className="flex items-center gap-1 text-primary hover:underline"
              >
                <Download className="size-3" aria-hidden />
                Download{op.rowCount != null ? ` (${op.rowCount} rows)` : ""}
              </a>
            )}
            {op.status === "failed" && (
              <span className="text-destructive">{op.error ?? "export failed"}</span>
            )}
          </span>
        )}
      </div>
    </Can>
  );
}
