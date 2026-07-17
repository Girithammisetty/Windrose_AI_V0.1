"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Boxes } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Badge } from "@/components/ui/primitives";
import { useModels } from "@/lib/graphql/hooks";
import { formatLocal } from "@/lib/utils";
import type { Model } from "@/lib/graphql/types";

export default function MlModelsPage() {
  const router = useRouter();
  const [productionOnly, setProductionOnly] = useState(false);
  const query = useModels({ stage: productionOnly ? "production" : undefined });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const columns: Column<Model>[] = [
    { id: "name", header: "Model", cell: (m) => <span className="font-medium">{m.name ?? m.id}</span> },
    {
      id: "type", header: "Type", width: 160,
      cell: (m) => (m.modelType ? <Badge variant="secondary">{m.modelType}</Badge> : <span className="text-muted-foreground">—</span>),
    },
    {
      id: "owner", header: "Owner", width: "1.5fr",
      cell: (m) => <span className="truncate font-mono text-xs text-muted-foreground">{m.ownerId ?? "—"}</span>,
    },
    { id: "created", header: "Registered", width: 170, cell: (m) => formatLocal(m.createdAt) },
  ];

  return (
    <div>
      <PageHeader
        title="Model registry"
        description="Registered models from the retrain loop. Open a model to see its versions, stages, and the promoted (production) version."
      />

      <div className="mb-3 flex items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={productionOnly}
            onChange={(e) => setProductionOnly(e.target.checked)}
            className="size-4 accent-[hsl(var(--primary))]"
          />
          Production models only
        </label>
      </div>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={productionOnly ? "No models in production yet." : "No registered models yet."}
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Registered models"
          rows={rows}
          columns={columns}
          rowId={(m) => m.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          onRowActivate={(m) => router.push(`/ml/models/${m.id}`)}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Boxes className="size-8" />
              <p>No registered models</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}
