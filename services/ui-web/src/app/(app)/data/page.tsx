"use client";
import { useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Database } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Input, Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useDatasets } from "@/lib/graphql/hooks";
import type { Dataset } from "@/lib/graphql/types";
import { formatLocal, formatNumber } from "@/lib/utils";

export default function DataDatasetsPage() {
  const router = useRouter();
  const params = useSearchParams();
  const q = params.get("q") ?? "";

  // URL is the source of truth for shareable view state (UI-FR-043).
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`/data?${next.toString()}`);
  };

  const query = useDatasets({ q: q || undefined });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const columns: Column<Dataset>[] = [
    { id: "name", header: "Name", cell: (d) => <span className="font-medium">{d.name}</span> },
    { id: "status", header: "Status", width: 130, cell: (d) => <StatusChip status={d.status} live /> },
    {
      id: "tags",
      header: "Tags",
      width: "1.5fr",
      cell: (d) => (
        <span className="flex flex-wrap gap-1">
          {d.tags.length === 0 ? "—" : d.tags.slice(0, 4).map((tag) => (
            <Badge key={tag} variant="secondary">{tag}</Badge>
          ))}
        </span>
      ),
    },
    { id: "rowCount", header: "Rows", width: 120, cell: (d) => formatNumber(d.rowCount), className: "tabular-nums" },
    { id: "created", header: "Created", width: 160, cell: (d) => formatLocal(d.createdAt) },
  ];

  return (
    <div>
      <PageHeader
        title="Datasets"
        description="Search the full dataset index; rows link to schema, profile, lineage, and query."
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search datasets…"
          defaultValue={q}
          onChange={(e) => setParam("q", e.target.value)}
          className="max-w-xs"
          aria-label="Search datasets"
        />
        {q && (
          <Button variant="ghost" size="sm" onClick={() => router.replace("/data")}>
            Clear
          </Button>
        )}
      </div>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No datasets yet"
        emptyCta={
          <Button variant="outline" size="sm" asChild>
            <a href="/data/connections">Create connection</a>
          </Button>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Datasets"
          rows={rows}
          columns={columns}
          rowId={(d) => d.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          onRowActivate={(d) => router.push(`/data/datasets/${d.id}`)}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Database className="size-8" />
              <p>No datasets</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}
