"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { LineChart, BookCheck, Plus, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Badge } from "@/components/ui/primitives";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useSemanticModelList, useDeleteSemanticModel } from "@/lib/graphql/hooks";
import { useSession } from "@/lib/session/SessionContext";
import { VerifiedQueriesPanel } from "@/components/semantic/VerifiedQueriesPanel";
import type { SemanticModelSummary } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

export default function SemanticModelsPage() {
  const router = useRouter();
  const { workspaceId } = useSession();
  const query = useSemanticModelList(workspaceId);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [tab, setTab] = useState<"models" | "verifiedQueries">("models");
  const [toDelete, setToDelete] = useState<SemanticModelSummary | null>(null);
  const deleteModel = useDeleteSemanticModel();

  const columns: Column<SemanticModelSummary>[] = [
    { id: "name", header: t("semantic.name"), cell: (m) => <span className="font-medium">{m.name}</span> },
    {
      id: "status",
      header: t("semantic.status"),
      width: 220,
      cell: (m) =>
        m.publishedVersionNo != null ? (
          <Badge variant="secondary">{t("semantic.published", { version: m.publishedVersionNo })}</Badge>
        ) : (
          <Badge variant="outline">{t("semantic.unpublished")}</Badge>
        ),
    },
    {
      id: "health",
      header: t("semantic.health"),
      width: 100,
      cell: (m) =>
        m.healthStatus ? (
          <Badge variant={m.healthStatus === "ok" ? "success" : "destructive"}>{m.healthStatus}</Badge>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    { id: "updated", header: t("pipelines.created"), width: 170, cell: (m) => formatLocal(m.updatedAt) },
    {
      id: "actions",
      header: "",
      width: 60,
      cell: (m) => (
        <Can gate={FEATURE_GATES.deleteSemanticModel}>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t("semantic.delete")}
            onClick={(e) => {
              e.stopPropagation();
              setToDelete(m);
            }}
          >
            <Trash2 className="text-destructive" />
          </Button>
        </Can>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t("semantic.title")}
        description={t("semantic.subtitle")}
        actions={
          tab === "models" ? (
            <Can gate={FEATURE_GATES.createSemanticModel}>
              <Button onClick={() => router.push("/data/semantic-models/new")}>
                <Plus /> {t("semantic.new")}
              </Button>
            </Can>
          ) : undefined
        }
      />

      <div className="mb-3 flex items-center gap-1" role="tablist" aria-label="Semantic area view">
        <Button
          role="tab"
          aria-selected={tab === "models"}
          variant={tab === "models" ? "default" : "ghost"}
          size="sm"
          onClick={() => setTab("models")}
        >
          {t("semantic.title")}
        </Button>
        <Can gate={FEATURE_GATES.viewVerifiedQueries}>
          <Button
            role="tab"
            aria-selected={tab === "verifiedQueries"}
            variant={tab === "verifiedQueries" ? "default" : "ghost"}
            size="sm"
            onClick={() => setTab("verifiedQueries")}
          >
            <BookCheck /> {t("semantic.tab.verifiedQueries")}
          </Button>
        </Can>
      </div>

      {tab === "verifiedQueries" ? (
        <VerifiedQueriesPanel />
      ) : (
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("semantic.empty")}
        emptyCta={
          <Can gate={FEATURE_GATES.createSemanticModel}>
            <Button className="mt-2" onClick={() => router.push("/data/semantic-models/new")}>
              <Plus /> {t("semantic.new")}
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("semantic.title")}
          rows={rows}
          columns={columns}
          rowId={(m) => m.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          onRowActivate={(m) => router.push(`/data/semantic-models/${m.id}`)}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <LineChart className="size-8" />
              <p>{t("semantic.emptyHint")}</p>
            </div>
          }
        />
      </AsyncBoundary>
      )}

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t("semantic.delete")}
        description={t("semantic.deleteConfirm", { name: toDelete?.name ?? "" })}
        confirmLabel={t("semantic.delete")}
        destructive
        onConfirm={() => {
          if (toDelete) deleteModel.mutate(toDelete.id, { onSettled: () => setToDelete(null) });
        }}
      />
    </div>
  );
}
