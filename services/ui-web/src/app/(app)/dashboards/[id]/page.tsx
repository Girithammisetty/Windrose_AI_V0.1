"use client";
import { use, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Pencil, Plus, Trash2, X } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ProvenanceBadge } from "@/components/primitives/ProvenanceBadge";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Card, CardContent, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { ChartView } from "@/components/charts/ChartView";
import { ChartEditor } from "@/components/charts/ChartEditor";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useDashboard, useDeleteChart, useDeleteDashboard, useArchiveDashboard } from "@/lib/graphql/hooks";
import type { Chart } from "@/lib/graphql/types";
import {
  type CrossFilter,
  crossFilterField,
  selectedValueFor,
  toggleCrossFilter,
  toFilterVars,
} from "@/lib/charts/crossfilter";
import { t } from "@/lib/i18n/messages";

export default function DashboardDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [filters, setFilters] = useState<CrossFilter[]>([]);
  const query = useDashboard(id, toFilterVars(filters));
  const dash = query.data?.dashboard;

  // origin chart id -> display name, for labelling the active-filter chips.
  const chartNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of dash?.charts ?? []) m.set(c.id, c.name ?? c.urn);
    return m;
  }, [dash]);

  const [editing, setEditing] = useState(false);
  const [editChart, setEditChart] = useState<Chart | null>(null);
  const [toDelete, setToDelete] = useState<Chart | null>(null);
  const deleteMutation = useDeleteChart(id);
  const [confirmDeleteDash, setConfirmDeleteDash] = useState(false);
  const [confirmArchiveDash, setConfirmArchiveDash] = useState(false);
  const deleteDashboard = useDeleteDashboard();
  const archiveDashboard = useArchiveDashboard();

  return (
    <div>
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && !dash}
        emptyTitle={t("dashboards.notFound")}
        onRetry={() => query.refetch()}
      >
        {dash && (
          <>
            <PageHeader
              title={dash.title}
              description={dash.urn}
              actions={
                <>
                  {dash.module && <Badge variant="secondary">{dash.module}</Badge>}
                  <Can gate={FEATURE_GATES.createDashboard}>
                    <Button
                      onClick={() => {
                        setEditChart(null);
                        setEditing(true);
                      }}
                    >
                      <Plus /> {t("dashboards.addChart")}
                    </Button>
                  </Can>
                  {!dash.archived && (
                    <Can gate={FEATURE_GATES.archiveDashboard}>
                      <Button variant="outline" onClick={() => setConfirmArchiveDash(true)}>
                        {t("dashboards.archive")}
                      </Button>
                    </Can>
                  )}
                  <Can gate={FEATURE_GATES.createDashboard}>
                    <Button variant="outline" onClick={() => setConfirmDeleteDash(true)}>
                      <Trash2 className="text-destructive" /> {t("dashboards.deleteDashboard")}
                    </Button>
                  </Can>
                </>
              }
            />

            {filters.length > 0 && (
              <div className="mb-3 flex flex-wrap items-center gap-2" aria-label={t("dashboards.activeFilters")}>
                <span className="text-xs text-muted-foreground">{t("dashboards.filteredBy")}</span>
                {filters.map((f) => (
                  <button
                    key={`${f.origin}:${f.field}`}
                    type="button"
                    onClick={() => setFilters((cur) => cur.filter((x) => x.origin !== f.origin))}
                    className="inline-flex items-center gap-1 rounded-full border bg-accent/40 px-2.5 py-0.5 text-xs hover:bg-accent"
                  >
                    <span className="text-muted-foreground">{chartNames.get(f.origin) ?? f.field}:</span>
                    <span className="font-medium">{f.value}</span>
                    <X className="size-3" aria-hidden />
                    <span className="sr-only">{t("dashboards.removeFilter")}</span>
                  </button>
                ))}
                <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => setFilters([])}>
                  {t("dashboards.clearFilters")}
                </Button>
              </div>
            )}

            {dash.charts.length === 0 ? (
              <div className="rounded-lg border border-dashed p-10 text-center">
                <p className="text-sm text-muted-foreground">{t("dashboards.noCharts")}</p>
                <Can gate={FEATURE_GATES.createDashboard}>
                  <Button
                    className="mt-3"
                    onClick={() => {
                      setEditChart(null);
                      setEditing(true);
                    }}
                  >
                    <Plus /> {t("dashboards.addChart")}
                  </Button>
                </Can>
              </div>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                {dash.charts.map((chart) => {
                  // A chart is a cross-filter source when we can resolve the
                  // group-by dimension its clicks should filter on.
                  const field = crossFilterField(chart.config, chart.data?.columns);
                  const onSelect = field
                    ? (value: string) => setFilters((cur) => toggleCrossFilter(cur, chart.id, field, value))
                    : undefined;
                  return (
                    <Card key={chart.id}>
                      <CardHeader className="flex-row items-start justify-between gap-2 space-y-0">
                        <div className="space-y-1">
                          <CardTitle className="text-sm">{chart.name ?? chart.urn}</CardTitle>
                          {chart.chartType && <Badge variant="outline">{chart.chartType}</Badge>}
                        </div>
                        <div className="flex items-center gap-2">
                          {/* AC-4: provenance badge on AI-generated charts. */}
                          <ProvenanceBadge provenance={chart.provenance} />
                          <Can gate={FEATURE_GATES.createDashboard}>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={t("dashboards.editChart")}
                              onClick={() => {
                                setEditChart(chart);
                                setEditing(true);
                              }}
                            >
                              <Pencil />
                            </Button>
                          </Can>
                          <Can gate={FEATURE_GATES.createDashboard}>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={t("dashboards.deleteChart")}
                              onClick={() => setToDelete(chart)}
                            >
                              <Trash2 className="text-destructive" />
                            </Button>
                          </Can>
                        </div>
                      </CardHeader>
                      <CardContent>
                        <ChartView
                          chartType={chart.chartType}
                          columns={chart.data?.columns}
                          rows={chart.data?.rows}
                          artifact={chart.data?.artifact}
                          title={chart.name ?? undefined}
                          onSelect={onSelect}
                          selectedValue={selectedValueFor(filters, chart.id)}
                        />
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}

            <ChartEditor
              dashboardId={id}
              open={editing}
              editChart={editChart}
              onOpenChange={(o) => {
                setEditing(o);
                if (!o) setEditChart(null);
              }}
              onSaved={() => query.refetch()}
            />

            <ConfirmDialog
              open={!!toDelete}
              onOpenChange={(o) => !o && setToDelete(null)}
              title={t("dashboards.deleteChart")}
              description={t("dashboards.deleteChartConfirm", { name: toDelete?.name ?? toDelete?.urn ?? "" })}
              confirmLabel={t("dashboards.deleteChart")}
              destructive
              onConfirm={() => {
                if (toDelete) deleteMutation.mutate(toDelete.id, { onSettled: () => setToDelete(null) });
              }}
            />

            <ConfirmDialog
              open={confirmArchiveDash}
              onOpenChange={setConfirmArchiveDash}
              title={t("dashboards.archive")}
              description={t("dashboards.archiveConfirm", { name: dash.title })}
              confirmLabel={t("dashboards.archive")}
              destructive
              onConfirm={() => {
                if (archiveDashboard.isPending) return;
                archiveDashboard.mutate(id, {
                  onSuccess: () => {
                    setConfirmArchiveDash(false);
                    router.push("/dashboards");
                  },
                });
              }}
            />

            <ConfirmDialog
              open={confirmDeleteDash}
              onOpenChange={setConfirmDeleteDash}
              title={t("dashboards.deleteDashboard")}
              description={t("dashboards.deleteDashboardConfirm", { name: dash.title })}
              confirmLabel={t("dashboards.deleteDashboard")}
              destructive
              onConfirm={() => {
                if (deleteDashboard.isPending) return;
                deleteDashboard.mutate(id, {
                  onSuccess: () => {
                    setConfirmDeleteDash(false);
                    router.push("/dashboards");
                  },
                });
              }}
            />
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}
