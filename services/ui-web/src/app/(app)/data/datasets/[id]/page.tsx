"use client";
import { use, useMemo, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { ExternalLink, Pencil, RefreshCcw } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { UrnLink } from "@/components/primitives/UrnLink";
import { NotWiredPanel } from "@/components/shell/NotWiredPanel";
import { DatasetRowsGrid } from "@/components/data/DatasetRowsGrid";
import { DatasetQuickChart } from "@/components/data/DatasetQuickChart";
import { EditDatasetDialog } from "@/components/data/EditDatasetDialog";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Can } from "@/components/authz/Can";
import { Card, CardContent, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useDataset,
  useDatasetLineage,
  useDatasetConsumers,
  useDatasetVersions,
  useSimilarDatasets,
  useReprofileDataset,
} from "@/lib/graphql/hooks";
import type { DatasetVersion } from "@/lib/graphql/types";
import { useHubTopics } from "@/lib/realtime/useHubTopics";
import { formatBytes, formatLocal, formatNumber } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

const TABS = ["overview", "data", "chart", "profile", "lineage", "consumers", "versions", "similar", "query"] as const;

export default function DatasetDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const query = useDataset(id);
  const d = query.data?.dataset;
  // Task #81 (the follow-up promised by the task #78 comment that used to live
  // here): dataset-service now emits dataset-status events keyed on the
  // dataset's own URN, realtime-hub routes `dataset.*` → run-status:<urn>, and
  // the datasetPatcher patches this page's cache. Subscribing here makes the
  // DRAFT → PROCESSING → READY/FAILED transition + the `<StatusChip … live>`
  // update without a refetch.
  useHubTopics(d?.urn ? [`run-status:${d.urn}`] : []);
  const [banner, setBanner] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const reprofile = useReprofileDataset();

  return (
    <div>
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && !d}
        emptyTitle="Dataset not found"
        onRetry={() => query.refetch()}
      >
        {d && (
          <>
            <PageHeader
              title={d.name}
              description={`${formatNumber(d.rowCount)} rows`}
              actions={
                <div className="flex items-center gap-2">
                  <Can gate={FEATURE_GATES.editDataset}>
                    <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
                      <Pencil />
                      {t("datasets.edit")}
                    </Button>
                  </Can>
                  <Can gate={FEATURE_GATES.reprofileDataset}>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={reprofile.isPending}
                      onClick={() =>
                        reprofile.mutate(
                          { id: d.id },
                          {
                            onSuccess: () => setBanner(t("datasets.reprofileStarted")),
                            onError: (e) => setBanner((e as Error).message),
                          },
                        )
                      }
                    >
                      <RefreshCcw />
                      {reprofile.isPending ? t("datasets.reprofiling") : t("datasets.reprofile")}
                    </Button>
                  </Can>
                  <StatusChip status={d.status} live />
                </div>
              }
            />

            <EditDatasetDialog
              open={editOpen}
              onOpenChange={setEditOpen}
              dataset={{ id: d.id, name: d.name, description: d.description }}
              onSaved={() => setBanner(t("datasets.editSaved"))}
            />

            {banner && (
              <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="notice-banner">
                {banner}
              </div>
            )}

            <Tabs.Root defaultValue="overview">
              <Tabs.List className="mb-3 flex gap-1 border-b" aria-label="Dataset sections">
                {TABS.map((v) => (
                  <Tabs.Trigger
                    key={v}
                    value={v}
                    className="border-b-2 border-transparent px-3 py-2 text-sm font-medium capitalize text-muted-foreground data-[state=active]:border-primary data-[state=active]:text-foreground"
                  >
                    {v}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>

              <Tabs.Content value="overview">
                <Card>
                  <CardContent className="grid grid-cols-2 gap-4 pt-4 text-sm">
                    <Field label="Name" value={d.name} />
                    <div>
                      <p className="text-muted-foreground">Status</p>
                      <StatusChip status={d.status} live />
                    </div>
                    <Field label="Rows" value={formatNumber(d.rowCount)} />
                    <Field label="Created" value={formatLocal(d.createdAt)} />
                    <div className="col-span-2">
                      <p className="mb-1 text-muted-foreground">Tags</p>
                      <span className="flex flex-wrap gap-1">
                        {d.tags.length === 0 ? "—" : d.tags.map((tag) => (
                          <Badge key={tag} variant="secondary">{tag}</Badge>
                        ))}
                      </span>
                    </div>
                    {d.description && (
                      <div className="col-span-2">
                        <p className="mb-1 text-muted-foreground">Description</p>
                        <p>{d.description}</p>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </Tabs.Content>

              <Tabs.Content value="data">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">Rows</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <DatasetRowsGrid datasetId={d.id} datasetUrn={d.urn} />
                  </CardContent>
                </Card>
              </Tabs.Content>

              <Tabs.Content value="chart">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">Quick chart</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <DatasetQuickChart datasetId={d.id} />
                  </CardContent>
                </Card>
              </Tabs.Content>

              <Tabs.Content value="profile">
                {d.profile ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-sm">Dataset profile</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4 text-sm">
                      <div className="grid grid-cols-2 gap-4">
                        <Field label="Profiled rows" value={formatNumber(d.profile.rowCount)} />
                        <Field label="Columns" value={formatNumber(d.profile.columnCount)} />
                      </div>
                      <div className="flex flex-wrap gap-3">
                        {d.profile.fullJsonUrl && (
                          <a
                            href={d.profile.fullJsonUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
                          >
                            Full profile JSON <ExternalLink className="size-3" aria-hidden />
                          </a>
                        )}
                        {d.profile.htmlReportUrl && (
                          <a
                            href={d.profile.htmlReportUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
                          >
                            HTML report <ExternalLink className="size-3" aria-hidden />
                          </a>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ) : (
                  <NotWiredPanel
                    title="No profile yet"
                    operation="dataset(id).profile"
                    description="Column-level profiling (row/column counts, distributions, regenerate-on-demand) appears here once the dataset has been profiled."
                  />
                )}
              </Tabs.Content>

              <Tabs.Content value="lineage">
                <LineageTab urn={d.urn} />
              </Tabs.Content>

              <Tabs.Content value="consumers">
                <ConsumersTab datasetId={d.id} />
              </Tabs.Content>

              <Tabs.Content value="versions">
                <VersionsTab datasetId={d.id} />
              </Tabs.Content>

              <Tabs.Content value="similar">
                <SimilarTab datasetId={d.id} />
              </Tabs.Content>

              <Tabs.Content value="query">
                <Card>
                  <CardContent className="flex flex-col items-start gap-3 pt-4 text-sm">
                    <p className="text-muted-foreground">
                      Run governed SQL against this dataset and the rest of the catalog in the query
                      workspace.
                    </p>
                    <Button size="sm" asChild>
                      <a href="/data/queries">Open query workspace</a>
                    </Button>
                  </CardContent>
                </Card>
              </Tabs.Content>
            </Tabs.Root>

            <div className="mt-4">
              <UrnLink urn={d.urn} label={d.name} />
            </div>
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-muted-foreground">{label}</p>
      <p className="font-medium">{value}</p>
    </div>
  );
}

/** Real lineage graph (dataset-service GET /lineage) rendered as an adjacency
 * list of upstream/downstream URN edges around this dataset. */
function LineageTab({ urn }: { urn: string }) {
  const query = useDatasetLineage(urn);
  const g = query.data;
  const edges = g?.edges ?? [];

  return (
    <AsyncBoundary
      isLoading={query.isLoading}
      isError={query.isError}
      error={query.error}
      isEmpty={!query.isLoading && edges.length === 0}
      emptyTitle="No lineage recorded yet"
      onRetry={() => query.refetch()}
    >
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            Lineage · {g?.nodes.length ?? 0} nodes, {edges.length} edges
            {g?.truncated && <Badge variant="warning" className="ml-2">truncated</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {edges.map((e, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
              <span className="font-mono text-xs">{shortUrn(e.fromUrn)}</span>
              <Badge variant="secondary">{e.activity ?? "→"}</Badge>
              <span className="text-muted-foreground">→</span>
              <span className="font-mono text-xs">{shortUrn(e.toUrn)}</span>
              {e.occurredAt && (
                <span className="ml-auto text-xs text-muted-foreground">{formatLocal(e.occurredAt)}</span>
              )}
            </div>
          ))}
        </CardContent>
      </Card>
    </AsyncBoundary>
  );
}

/** wr:tenant:svc:type/id -> svc:type/id (drop the tenant for readability). */
function shortUrn(u: string): string {
  const parts = u.split(":");
  return parts.length >= 4 ? `${parts[2]}:${parts.slice(3).join(":")}` : u;
}

/** Who reads this dataset: depth-3 downstream lineage rollup (dataset-service
 * GET /datasets/{id}/consumers) counted by service and activity. */
function ConsumersTab({ datasetId }: { datasetId: string }) {
  const query = useDatasetConsumers(datasetId);
  const c = query.data;
  const entries = (m?: Record<string, number>) => Object.entries(m ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <AsyncBoundary
      isLoading={query.isLoading}
      isError={query.isError}
      error={query.error}
      isEmpty={!query.isLoading && (c?.downstreamEdges ?? 0) === 0}
      emptyTitle={t("datasets.consumers.empty")}
      onRetry={() => query.refetch()}
    >
      {c && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              {t("datasets.consumers.edges", { count: c.downstreamEdges })}
              {c.truncated && (
                <Badge variant="warning" className="ml-2">
                  {t("datasets.consumers.truncated")}
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
            <div>
              <p className="mb-2 font-medium">{t("datasets.consumers.byService")}</p>
              <ul className="space-y-1">
                {entries(c.byService).map(([svc, n]) => (
                  <li key={svc} className="flex items-center justify-between rounded-md border px-3 py-1.5">
                    <span className="font-mono text-xs">{svc}</span>
                    <span className="tabular-nums">{formatNumber(n)}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-2 font-medium">{t("datasets.consumers.byActivity")}</p>
              <ul className="space-y-1">
                {entries(c.byActivity).map(([act, n]) => (
                  <li key={act} className="flex items-center justify-between rounded-md border px-3 py-1.5">
                    <span className="font-mono text-xs">{act}</span>
                    <span className="tabular-nums">{formatNumber(n)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </CardContent>
        </Card>
      )}
    </AsyncBoundary>
  );
}

/** Immutable version history (dataset-service GET /datasets/{id}/versions). */
function VersionsTab({ datasetId }: { datasetId: string }) {
  const query = useDatasetVersions(datasetId);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const columns: Column<DatasetVersion>[] = [
    { id: "version", header: t("datasets.versions.version"), width: 90, cell: (v) => <Badge variant="secondary">v{v.versionNo}</Badge> },
    {
      id: "snapshot",
      header: t("datasets.versions.snapshot"),
      cell: (v) => <span className="truncate font-mono text-xs">{v.icebergSnapshotId ?? "—"}</span>,
    },
    { id: "rows", header: t("datasets.versions.rows"), width: 110, className: "tabular-nums", cell: (v) => formatNumber(v.rowCount) },
    { id: "bytes", header: t("datasets.versions.bytes"), width: 100, cell: (v) => formatBytes(v.bytes) },
    {
      id: "breaking",
      header: t("datasets.versions.breaking"),
      width: 100,
      cell: (v) => (v.breakingChange ? <Badge variant="destructive">yes</Badge> : <span className="text-muted-foreground">no</span>),
    },
    {
      id: "profile",
      header: t("datasets.versions.profile"),
      width: 120,
      cell: (v) => (v.profileStatus ? <StatusChip status={v.profileStatus.toUpperCase()} /> : "—"),
    },
    { id: "created", header: t("datasets.versions.created"), width: 170, cell: (v) => formatLocal(v.createdAt) },
  ];

  return (
    <AsyncBoundary
      isLoading={query.isLoading}
      isError={query.isError}
      error={query.error}
      isEmpty={rows.length === 0}
      emptyTitle={t("datasets.versions.empty")}
      onRetry={() => query.refetch()}
    >
      <DataTable
        ariaLabel="Dataset versions"
        rows={rows}
        columns={columns}
        rowId={(v) => v.id}
        hasMore={query.hasNextPage}
        isFetchingMore={query.isFetchingNextPage}
        onLoadMore={() => query.fetchNextPage()}
      />
    </AsyncBoundary>
  );
}

/** Similar datasets ranked by schema similarity (dataset-service POST
 * /datasets:similar over this dataset's real columns). */
function SimilarTab({ datasetId }: { datasetId: string }) {
  const query = useSimilarDatasets(datasetId);
  const rows = query.data ?? [];

  return (
    <AsyncBoundary
      isLoading={query.isLoading}
      isError={query.isError}
      error={query.error}
      isEmpty={!query.isLoading && rows.length === 0}
      emptyTitle={t("datasets.similar.empty")}
      onRetry={() => query.refetch()}
    >
      <Card>
        <CardContent className="space-y-2 pt-4 text-sm">
          {rows.map((s, i) => (
            <div key={s.id ?? s.urn ?? i} className="flex items-center justify-between gap-2 rounded-md border p-2">
              <span className="min-w-0">
                {s.id ? (
                  <a href={`/data/datasets/${s.id}`} className="truncate font-medium text-primary hover:underline">
                    {s.name ?? s.id}
                  </a>
                ) : (
                  <span className="truncate font-medium">{s.name ?? s.urn}</span>
                )}
              </span>
              {s.score != null && (
                <Badge variant="secondary">
                  {t("datasets.similar.score")}: {(s.score * 100).toFixed(0)}%
                </Badge>
              )}
            </div>
          ))}
        </CardContent>
      </Card>
    </AsyncBoundary>
  );
}
