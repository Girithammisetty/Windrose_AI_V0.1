"use client";
import { useMemo, useState } from "react";
import { Play, Clock, Save, History, Pencil, Trash2, Layers } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, Textarea, Badge } from "@/components/ui/primitives";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useSavedQueries,
  useSavedQuery,
  useRunSql,
  useRunSavedQuery,
  useDeleteSavedQuery,
} from "@/lib/graphql/hooks";
import type { QueryResult, SavedQuery, SavedQueryVersion } from "@/lib/graphql/types";
import { SavedQueryDialog } from "@/components/queries/SavedQueryDialog";
import { VersionsDialog } from "@/components/queries/VersionsDialog";
import { ExecutionsPanel } from "@/components/queries/ExecutionsPanel";
import { formatLocal, formatNumber } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

const DEFAULT_SQL = "SELECT 1 AS example";

export default function QueriesPage() {
  const saved = useSavedQueries();
  const runSql = useRunSql();
  const runSaved = useRunSavedQuery();
  const deleteMutation = useDeleteSavedQuery();
  const [sql, setSql] = useState(DEFAULT_SQL);
  const [activeName, setActiveName] = useState<string | null>(null);
  const [tab, setTab] = useState<"editor" | "executions">("editor");
  const [banner, setBanner] = useState<string | null>(null);

  // Authoring dialogs. Editing hydrates the full query (sqlText/variables)
  // through the single-resource path before the form opens.
  const [saveOpen, setSaveOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const editDetail = useSavedQuery(editId ?? "");
  const [versionsFor, setVersionsFor] = useState<SavedQuery | null>(null);
  const [toDelete, setToDelete] = useState<SavedQuery | null>(null);

  const savedRows = useMemo(() => saved.data?.pages.flatMap((p) => p.nodes) ?? [], [saved.data]);

  // Whichever run mutation last resolved owns the results panel.
  const pending = runSql.isPending || runSaved.isPending;
  const result: QueryResult | undefined = runSql.data ?? runSaved.data;
  const runError = (runSql.error ?? runSaved.error) as Error | null;

  const execAdhoc = () => {
    setActiveName(null);
    runSql.mutate({ sql, limit: 1000 });
  };

  const execSaved = (q: SavedQuery) => {
    setActiveName(q.name);
    runSaved.mutate({ id: q.id, limit: 1000 });
  };

  const onSaved = (q: SavedQuery) => {
    setSaveOpen(false);
    setEditId(null);
    setBanner(t("queries.saved", { version: q.versionNo ?? 1 }));
  };

  const loadVersion = (v: SavedQueryVersion) => {
    if (v.sqlText) {
      setSql(v.sqlText);
      setActiveName(null);
      setVersionsFor(null);
      setTab("editor");
    }
  };

  return (
    <div>
      <PageHeader
        title="Queries"
        description="Run ad-hoc SQL against the governed query engine, author saved queries, and review execution history."
      />

      <div className="mb-3 flex items-center gap-1" role="tablist" aria-label="Queries view">
        <Button
          role="tab"
          aria-selected={tab === "editor"}
          variant={tab === "editor" ? "default" : "ghost"}
          size="sm"
          onClick={() => setTab("editor")}
        >
          {t("queries.tab.editor")}
        </Button>
        <Can gate={FEATURE_GATES.viewQueryExecutions}>
          <Button
            role="tab"
            aria-selected={tab === "executions"}
            variant={tab === "executions" ? "default" : "ghost"}
            size="sm"
            onClick={() => setTab("executions")}
          >
            <History /> {t("queries.tab.executions")}
          </Button>
        </Can>
      </div>

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="notice-banner">
          {banner}
        </div>
      )}

      {tab === "executions" ? (
        <ExecutionsPanel savedQueries={savedRows} onNotice={setBanner} />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_20rem]">
          <div className="space-y-4">
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-sm">
                  {activeName ? `Saved query · ${activeName}` : "Ad-hoc SQL"}
                </CardTitle>
                <div className="flex items-center gap-2">
                  <Can gate={FEATURE_GATES.createSavedQuery}>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setSaveOpen(true)}
                      disabled={sql.trim().length === 0}
                    >
                      <Save /> {t("queries.save")}
                    </Button>
                  </Can>
                  <Button size="sm" onClick={execAdhoc} disabled={pending || sql.trim().length === 0}>
                    <Play /> {pending ? "Running…" : "Run"}
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                <Textarea
                  aria-label="SQL editor"
                  rows={6}
                  className="font-mono text-xs"
                  value={sql}
                  onChange={(e) => {
                    setSql(e.target.value);
                    setActiveName(null);
                  }}
                  spellCheck={false}
                />
                <p className="text-xs text-muted-foreground">
                  Read-only statements only. Reference datasets by their published name; results cap at 1,000 rows.
                </p>
              </CardContent>
            </Card>

            <ResultPanel result={result} error={runError} isPending={pending} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Saved queries</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <AsyncBoundary
                isLoading={saved.isLoading}
                isError={saved.isError}
                error={saved.error}
                isEmpty={savedRows.length === 0}
                emptyTitle="No saved queries yet"
                onRetry={() => saved.refetch()}
              >
                <ul className="divide-y">
                  {savedRows.map((q) => (
                    <li key={q.id} className="px-4 py-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{q.name}</p>
                          <p className="flex items-center gap-1 text-xs text-muted-foreground">
                            <Clock className="size-3" /> {formatLocal(q.updatedAt)}
                            {q.versionNo != null && <Badge variant="secondary">v{q.versionNo}</Badge>}
                          </p>
                        </div>
                        <Button size="sm" variant="outline" onClick={() => execSaved(q)} disabled={pending}>
                          <Play /> Run
                        </Button>
                      </div>
                      <div className="mt-1 flex items-center gap-1">
                        <Can gate={FEATURE_GATES.updateSavedQuery}>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Edit ${q.name}`}
                            onClick={() => setEditId(q.id)}
                          >
                            <Pencil /> {t("queries.edit")}
                          </Button>
                        </Can>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Versions of ${q.name}`}
                          onClick={() => setVersionsFor(q)}
                        >
                          <Layers /> {t("queries.versions")}
                        </Button>
                        <Can gate={FEATURE_GATES.deleteSavedQuery}>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Delete ${q.name}`}
                            onClick={() => setToDelete(q)}
                          >
                            <Trash2 /> {t("queries.delete")}
                          </Button>
                        </Can>
                      </div>
                    </li>
                  ))}
                </ul>
              </AsyncBoundary>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Create (from the editor's SQL) */}
      <SavedQueryDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        initialSql={sql}
        onSaved={onSaved}
      />
      {/* Edit — waits for the detail fetch so sqlText/variables are real. */}
      <SavedQueryDialog
        open={!!editId && !!editDetail.data?.savedQuery}
        onOpenChange={(o) => !o && setEditId(null)}
        initialSql=""
        editing={editDetail.data?.savedQuery ?? null}
        onSaved={onSaved}
      />
      <VersionsDialog
        query={versionsFor}
        onOpenChange={(o) => !o && setVersionsFor(null)}
        onLoad={loadVersion}
      />
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t("queries.delete")}
        description={toDelete ? t("queries.deleteConfirm", { name: toDelete.name }) : ""}
        confirmLabel={t("queries.delete")}
        destructive
        onConfirm={() => {
          if (toDelete)
            deleteMutation.mutate(toDelete.id, {
              onSuccess: () => setBanner(t("queries.deleted")),
              onSettled: () => setToDelete(null),
            });
        }}
      />
    </div>
  );
}

function ResultPanel({
  result,
  error,
  isPending,
}: {
  result?: QueryResult;
  error: Error | null;
  isPending: boolean;
}) {
  if (isPending) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">Executing query…</CardContent>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-destructive">{error.message}</CardContent>
      </Card>
    );
  }
  if (!result) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">
          Run a query to see results here.
        </CardContent>
      </Card>
    );
  }
  if (result.status !== "succeeded") {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-destructive">
          Execution {result.status}
          {result.error ? `: ${JSON.stringify(result.error)}` : ""}
        </CardContent>
      </Card>
    );
  }

  const rows = (result.rows ?? []) as unknown[][];
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle className="text-sm">Results</CardTitle>
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="tabular-nums">{formatNumber(result.resultRows ?? rows.length)} rows</span>
          {result.engine && <Badge variant="secondary">{result.engine}</Badge>}
          {result.cacheHit && <Badge variant="secondary">cache</Badge>}
          {result.durationMs != null && <span className="tabular-nums">{result.durationMs} ms</span>}
        </p>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-left">
                {result.columns.map((c) => (
                  <th key={c.name} className="whitespace-nowrap px-3 py-2 font-medium">
                    {c.name}
                    {c.type && <span className="ml-1 font-normal text-muted-foreground">{c.type}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td className="px-3 py-6 text-muted-foreground" colSpan={Math.max(result.columns.length, 1)}>
                    Query returned no rows.
                  </td>
                </tr>
              ) : (
                rows.map((row, i) => (
                  <tr key={i} className="border-b last:border-0">
                    {result.columns.map((c, j) => (
                      <td key={c.name} className="whitespace-nowrap px-3 py-1.5 font-mono text-xs tabular-nums">
                        {formatCell(Array.isArray(row) ? row[j] : (row as Record<string, unknown>)[c.name])}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {result.hasMore && (
          <p className="border-t px-3 py-2 text-xs text-muted-foreground">
            Showing the first page; refine the query to narrow results.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function formatCell(v: unknown): string {
  if (v == null) return "∅";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
