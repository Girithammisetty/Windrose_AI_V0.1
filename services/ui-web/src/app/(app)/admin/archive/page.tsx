"use client";
import { useMemo, useState } from "react";
import { X, Database } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Card, CardHeader, CardTitle, CardContent, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useSession } from "@/lib/session/SessionContext";
import {
  useWorkspaces,
  useArchivedDashboards,
  useRestoreDashboard,
  useArchivedExperiments,
  useRestoreExperiment,
  useArchiveDataset,
  useRestoreDataset,
} from "@/lib/graphql/hooks";
import type { Workspace, Dashboard, Experiment } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

export default function AdminArchivePage() {
  const { workspaceId } = useSession();
  return (
    <div>
      <PageHeader
        title="Archive"
        description="Soft-deleted resources across rbac-service, chart-service, experiment-service and dataset-service."
      />
      <div className="grid gap-4">
        <ArchivedWorkspaces />
        <ArchivedDashboards workspaceId={workspaceId} />
        <ArchivedExperiments workspaceId={workspaceId} />
        <DatasetArchiveRestore />
      </div>
    </div>
  );
}

function ArchivedWorkspaces() {
  const query = useWorkspaces({ archived: "only" });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const columns: Column<Workspace>[] = [
    { id: "name", header: "Name", cell: (w) => <span className="font-medium">{w.name}</span> },
    { id: "archivedAt", header: "Archived", width: 180, cell: (w) => formatLocal(w.archivedAt) },
    { id: "createdBy", header: "Created by", width: 200, cell: (w) => w.createdBy || <span className="text-muted-foreground">—</span> },
  ];

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">Archived workspaces (rbac-service)</CardTitle></CardHeader>
      <CardContent>
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={rows.length === 0}
          emptyTitle="No archived workspaces."
          onRetry={() => query.refetch()}
        >
          <DataTable
            ariaLabel="Archived workspaces"
            rows={rows}
            columns={columns}
            rowId={(w) => w.id}
            hasMore={query.hasNextPage}
            isFetchingMore={query.isFetchingNextPage}
            onLoadMore={() => query.fetchNextPage()}
          />
        </AsyncBoundary>
      </CardContent>
    </Card>
  );
}

function ArchivedDashboards({ workspaceId }: { workspaceId: string }) {
  const query = useArchivedDashboards(workspaceId);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [selected, setSelected] = useState<Dashboard | null>(null);
  const restore = useRestoreDashboard();
  const [banner, setBanner] = useState<string | null>(null);

  const columns: Column<Dashboard>[] = [
    { id: "title", header: "Title", cell: (d) => <span className="font-medium">{d.title}</span> },
    { id: "module", header: "Module", width: 140, cell: (d) => d.module || <span className="text-muted-foreground">—</span> },
  ];

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">Archived dashboards (chart-service)</CardTitle></CardHeader>
      <CardContent className="grid gap-3 lg:grid-cols-[1fr_320px]">
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={rows.length === 0}
          emptyTitle="No archived dashboards."
          onRetry={() => query.refetch()}
        >
          <DataTable
            ariaLabel="Archived dashboards"
            rows={rows}
            columns={columns}
            rowId={(d) => d.id}
            onRowActivate={(d) => setSelected(d)}
            hasMore={query.hasNextPage}
            isFetchingMore={query.isFetchingNextPage}
            onLoadMore={() => query.fetchNextPage()}
          />
        </AsyncBoundary>
        <Card className="h-fit">
          {selected ? (
            <>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-sm">{selected.title}</CardTitle>
                <Button variant="ghost" size="sm" onClick={() => setSelected(null)} aria-label="Close"><X className="size-4" /></Button>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Can gate={FEATURE_GATES.archiveDashboard}>
                  <Button
                    size="sm"
                    disabled={restore.isPending}
                    onClick={() =>
                      restore.mutate(selected.id, {
                        onSuccess: () => { setBanner(`Restored "${selected.title}".`); setSelected(null); },
                        onError: (e) => setBanner(e.message),
                      })
                    }
                  >
                    Restore
                  </Button>
                </Can>
              </CardContent>
            </>
          ) : (
            <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
              <p>Select a dashboard to restore it.</p>
            </CardContent>
          )}
        </Card>
        {banner && <p role="status" className="text-xs text-muted-foreground lg:col-span-2">{banner}</p>}
      </CardContent>
    </Card>
  );
}

function ArchivedExperiments({ workspaceId }: { workspaceId: string }) {
  const query = useArchivedExperiments({ workspaceId });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [selected, setSelected] = useState<Experiment | null>(null);
  const restore = useRestoreExperiment();
  const [banner, setBanner] = useState<string | null>(null);

  const columns: Column<Experiment>[] = [
    { id: "name", header: "Name", cell: (e) => <span className="font-medium">{e.name}</span> },
    { id: "desc", header: "Description", cell: (e) => e.description || <span className="text-muted-foreground">—</span> },
  ];

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">Archived experiments (experiment-service)</CardTitle></CardHeader>
      <CardContent className="grid gap-3 lg:grid-cols-[1fr_320px]">
        <AsyncBoundary
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          isEmpty={rows.length === 0}
          emptyTitle="No archived experiments."
          onRetry={() => query.refetch()}
        >
          <DataTable
            ariaLabel="Archived experiments"
            rows={rows}
            columns={columns}
            rowId={(e) => e.id}
            onRowActivate={(e) => setSelected(e)}
            hasMore={query.hasNextPage}
            isFetchingMore={query.isFetchingNextPage}
            onLoadMore={() => query.fetchNextPage()}
          />
        </AsyncBoundary>
        <Card className="h-fit">
          {selected ? (
            <>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-sm">{selected.name}</CardTitle>
                <Button variant="ghost" size="sm" onClick={() => setSelected(null)} aria-label="Close"><X className="size-4" /></Button>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Can gate={FEATURE_GATES.restoreExperiment}>
                  <Button
                    size="sm"
                    disabled={restore.isPending}
                    onClick={() =>
                      restore.mutate(selected.id, {
                        onSuccess: () => { setBanner(`Restored "${selected.name}".`); setSelected(null); },
                        onError: (e) => setBanner(e.message),
                      })
                    }
                  >
                    Restore
                  </Button>
                </Can>
              </CardContent>
            </>
          ) : (
            <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
              <p>Select an experiment to restore it.</p>
            </CardContent>
          )}
        </Card>
        {banner && <p role="status" className="text-xs text-muted-foreground lg:col-span-2">{banner}</p>}
      </CardContent>
    </Card>
  );
}

function DatasetArchiveRestore() {
  const archive = useArchiveDataset();
  const restore = useRestoreDataset();
  const [archiveId, setArchiveId] = useState("");
  const [restoreId, setRestoreId] = useState("");
  const [banner, setBanner] = useState<string | null>(null);

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">Datasets (dataset-service)</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex items-start gap-2 rounded-md border border-dashed bg-muted/40 p-3 text-xs text-muted-foreground">
          <Database className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>
            dataset-service exposes no archived-only list read (<code className="font-mono">GET /datasets</code> has
            no deleted/archived filter), so archived datasets cannot be enumerated here. The archive and restore
            mutations are real and wired below — use a dataset id (e.g. copied from the audit trail) to archive or
            restore it directly.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Can gate={FEATURE_GATES.archiveDataset}>
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (archiveId.trim())
                  archive.mutate(
                    { id: archiveId.trim() },
                    {
                      onSuccess: () => { setBanner(`Archived dataset ${archiveId.trim()}.`); setArchiveId(""); },
                      onError: (err) => setBanner(err.message),
                    },
                  );
              }}
            >
              <label className="flex flex-1 flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Dataset id to archive</span>
                <Input value={archiveId} onChange={(e) => setArchiveId(e.target.value)} aria-label="Dataset id to archive" className="h-8 text-xs" />
              </label>
              <Button type="submit" size="sm" variant="outline" disabled={!archiveId.trim() || archive.isPending}>Archive</Button>
            </form>
          </Can>

          <Can gate={FEATURE_GATES.restoreDataset}>
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (restoreId.trim())
                  restore.mutate(restoreId.trim(), {
                    onSuccess: (r) => { setBanner(`Restored dataset as "${r.restoreDataset.name}".`); setRestoreId(""); },
                    onError: (err) => setBanner(err.message),
                  });
              }}
            >
              <label className="flex flex-1 flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Dataset id to restore</span>
                <Input value={restoreId} onChange={(e) => setRestoreId(e.target.value)} aria-label="Dataset id to restore" className="h-8 text-xs" />
              </label>
              <Button type="submit" size="sm" disabled={!restoreId.trim() || restore.isPending}>Restore</Button>
            </form>
          </Can>
        </div>
        {banner && <p role="status" className="text-xs text-muted-foreground">{banner}</p>}
      </CardContent>
    </Card>
  );
}
