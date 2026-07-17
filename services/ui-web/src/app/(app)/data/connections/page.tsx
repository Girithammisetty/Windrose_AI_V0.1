"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Plug, Plus, Loader2 } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES, cap } from "@/lib/authz/registry";
import { useConnections, useConnectorTypes, useTestConnection, useDeleteConnection } from "@/lib/graphql/hooks";
import type { DataConnection } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

export default function DataConnectionsPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [connectorType, setConnectorType] = useState("");
  const filter = useMemo(() => ({ q: q || undefined, connectorType: connectorType || undefined }), [q, connectorType]);

  const query = useConnections(filter);
  const catalog = useConnectorTypes();
  const testMutation = useTestConnection();
  const deleteMutation = useDeleteConnection();

  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const displayName = (ct: string) =>
    catalog.data?.find((x) => x.connectorType === ct)?.displayName ?? ct;

  const [testingId, setTestingId] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<DataConnection | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const onTest = (c: DataConnection) => {
    setTestingId(c.id);
    setBanner(null);
    testMutation.mutate(
      { id: c.id },
      {
        onSuccess: (r) =>
          setBanner(
            r.status === "OK"
              ? `${c.name}: ${t("connections.testOk", { ms: r.latencyMs ?? 0 })}`
              : `${c.name}: ${t("connections.testFailed")}${r.errorCategory ? ` — ${r.errorCategory}` : ""}`,
          ),
        onError: (e) => setBanner(`${c.name}: ${e.message}`),
        onSettled: () => {
          setTestingId(null);
          query.refetch();
        },
      },
    );
  };

  const columns: Column<DataConnection>[] = [
    { id: "name", header: t("connections.name"), cell: (c) => <span className="font-medium">{c.name}</span> },
    { id: "type", header: t("connections.type"), width: 170, cell: (c) => displayName(c.connectorType) },
    {
      id: "status",
      header: t("connections.status"),
      width: 120,
      cell: (c) =>
        c.lastTestStatus ? (
          <StatusChip status={c.lastTestStatus === "ok" ? "SUCCEEDED" : "FAILED"} />
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    { id: "tested", header: t("connections.lastTested"), width: 170, cell: (c) => formatLocal(c.lastTestedAt) },
    {
      id: "actions",
      header: "",
      width: 230,
      cell: (c) => (
        <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          <Button variant="outline" size="sm" onClick={() => onTest(c)} disabled={testingId === c.id}>
            {testingId === c.id ? <Loader2 className="animate-spin" /> : t("connections.test")}
          </Button>
          <Can gate={FEATURE_GATES.updateConnection}>
            <Button variant="ghost" size="sm" onClick={() => router.push(`/data/connections/${c.id}`)}>
              {t("connections.edit")}
            </Button>
          </Can>
          <Can gate={cap("ingestion.connection.delete")}>
            <Button variant="ghost" size="sm" onClick={() => setToDelete(c)}>
              {t("connections.delete")}
            </Button>
          </Can>
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t("connections.title")}
        description={t("connections.subtitle")}
        actions={
          <Can gate={FEATURE_GATES.createConnection}>
            <Button onClick={() => router.push("/data/connections/new")}>
              <Plus /> {t("connections.new")}
            </Button>
          </Can>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search connections…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-xs"
          aria-label="Search connections"
        />
        <label className="flex items-center gap-1 text-sm">
          <span className="text-muted-foreground">{t("connections.type")}</span>
          <select
            value={connectorType}
            onChange={(e) => setConnectorType(e.target.value)}
            aria-label="Filter by type"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="">all</option>
            {(catalog.data ?? []).map((ctype) => (
              <option key={ctype.connectorType} value={ctype.connectorType}>
                {ctype.displayName}
              </option>
            ))}
          </select>
        </label>
        {(q || connectorType) && (
          <Button variant="ghost" size="sm" onClick={() => { setQ(""); setConnectorType(""); }}>
            Clear
          </Button>
        )}
      </div>

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="test-banner">
          {banner}
        </div>
      )}

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("connections.empty")}
        emptyCta={
          <Can gate={FEATURE_GATES.createConnection}>
            <Button className="mt-2" onClick={() => router.push("/data/connections/new")}>
              <Plus /> {t("connections.new")}
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("connections.title")}
          rows={rows}
          columns={columns}
          rowId={(c) => c.id}
          onRowActivate={(c) => router.push(`/data/connections/${c.id}`)}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Plug className="size-8" />
              <p>{t("connections.emptyHint")}</p>
            </div>
          }
        />
      </AsyncBoundary>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t("connections.delete")}
        description={toDelete ? t("connections.deleteConfirm", { name: toDelete.name }) : ""}
        confirmLabel={t("connections.delete")}
        destructive
        onConfirm={() => {
          if (toDelete)
            deleteMutation.mutate(toDelete.id, {
              onSuccess: () => setBanner(t("connections.deleted")),
              onSettled: () => setToDelete(null),
            });
        }}
      />
    </div>
  );
}
