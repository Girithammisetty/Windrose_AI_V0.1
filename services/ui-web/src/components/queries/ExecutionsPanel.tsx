"use client";
import { useMemo, useState } from "react";
import { History } from "lucide-react";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useQueryExecutions, useCancelQueryExecution } from "@/lib/graphql/hooks";
import type { QueryExecution, SavedQuery } from "@/lib/graphql/types";
import { formatBytes, formatLocal, formatNumber } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

// query-service stores statuses lowercase and the filter is exact-match.
const EXEC_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const;
const CANCELLABLE = new Set(["queued", "running"]);

/**
 * Execution history (query-service GET /executions): every ad-hoc/saved run
 * with status, timing and scan cost; queued/running rows expose a real
 * cancel (POST /executions/{id}/cancel).
 */
export function ExecutionsPanel({
  savedQueries,
  onNotice,
}: {
  savedQueries: SavedQuery[];
  onNotice: (msg: string) => void;
}) {
  const [status, setStatus] = useState("");
  const vars = useMemo(() => ({ status: status || undefined }), [status]);
  const query = useQueryExecutions(vars);
  const cancelMutation = useCancelQueryExecution();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const queryName = useMemo(() => {
    const m = new Map<string, string>();
    for (const q of savedQueries) m.set(q.id, q.name);
    return m;
  }, [savedQueries]);

  const columns: Column<QueryExecution>[] = [
    {
      id: "query",
      header: t("queries.execQuery"),
      cell: (e) => (
        <span className="truncate text-sm">
          {e.savedQueryId ? (
            <>
              {queryName.get(e.savedQueryId) ?? <span className="font-mono text-xs">{e.savedQueryId}</span>}
              {e.queryVersionNo != null && (
                <span className="ml-1 text-xs text-muted-foreground">v{e.queryVersionNo}</span>
              )}
            </>
          ) : (
            <span className="text-muted-foreground">{t("queries.execAdhoc")}</span>
          )}
        </span>
      ),
    },
    {
      id: "status",
      header: t("queries.execStatus"),
      width: 120,
      cell: (e) => <StatusChip status={e.status.toUpperCase()} />,
    },
    { id: "started", header: t("queries.execStarted"), width: 160, cell: (e) => formatLocal(e.startedAt ?? e.createdAt) },
    {
      id: "duration",
      header: t("queries.execDuration"),
      width: 100,
      cell: (e) => (e.durationMs != null ? `${e.durationMs} ms` : "—"),
    },
    {
      id: "rows",
      header: t("queries.execRows"),
      width: 90,
      cell: (e) => (e.resultRows != null ? formatNumber(e.resultRows) : "—"),
    },
    {
      id: "scanned",
      header: t("queries.execScanned"),
      width: 100,
      cell: (e) => (e.scanBytes != null ? formatBytes(e.scanBytes) : "—"),
    },
    { id: "engine", header: t("queries.execEngine"), width: 100, cell: (e) => e.engine ?? "—" },
    {
      id: "actions",
      header: "",
      width: 100,
      cell: (e) =>
        CANCELLABLE.has(e.status) ? (
          <div className="flex justify-end" onClick={(ev) => ev.stopPropagation()}>
            <Can gate={FEATURE_GATES.cancelQueryExecution}>
              <Button
                variant="ghost"
                size="sm"
                disabled={cancelMutation.isPending}
                onClick={() =>
                  cancelMutation.mutate(e.id, { onSuccess: () => onNotice(t("queries.execCancelled")) })
                }
              >
                {t("queries.execCancel")}
              </Button>
            </Can>
          </div>
        ) : null,
    },
  ];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-sm">
          <span className="text-muted-foreground">{t("queries.execFilterStatus")}</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by execution status"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="">all</option>
            {EXEC_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("queries.executionsEmpty")}
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("queries.executionsTitle")}
          rows={rows}
          columns={columns}
          rowId={(e) => e.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <History className="size-8" />
              <p>{t("queries.executionsEmptyHint")}</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}
