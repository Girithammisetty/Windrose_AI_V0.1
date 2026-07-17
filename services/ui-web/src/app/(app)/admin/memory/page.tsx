"use client";
import { useMemo, useState } from "react";
import { Brain, ShieldAlert, X } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardContent, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useMemories, useMemory, useMemoryStats, useRequestMemoryErasure, useErasure,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { MemoryRecord } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";
import { formatLocal } from "@/lib/utils";

const SCOPES = ["", "session", "user", "workspace", "tenant"];
const STATUSES = ["", "active", "quarantined", "expired", "deleted"];
const STATUS_VARIANT: Record<string, "success" | "warning" | "secondary" | "destructive"> = {
  active: "success",
  quarantined: "warning",
  expired: "secondary",
  deleted: "destructive",
};

export default function AdminMemoryPage() {
  return (
    <div>
      <PageHeader title={t("memory.title")} description={t("memory.subtitle")} />
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <MemoryBrowseCard />
        <div className="space-y-4">
          <ErasureCard />
          <StatsCard />
        </div>
      </div>
    </div>
  );
}

function MemoryBrowseCard() {
  const [scope, setScope] = useState("");
  const [scopeRef, setScopeRef] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<MemoryRecord | null>(null);

  const query = useMemories({
    scope: scope || undefined,
    scopeRef: scopeRef.trim() || undefined,
    status: status || undefined,
  });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const columns: Column<MemoryRecord>[] = [
    { id: "scope", header: "Scope", width: 100, cell: (m) => <span className="font-mono text-xs">{m.scope}</span> },
    { id: "scopeRef", header: "Scope ref", width: 160, cell: (m) => <span className="truncate font-mono text-xs">{m.scopeRef}</span> },
    { id: "content", header: "Content", cell: (m) => <span className="truncate">{m.content}</span> },
    { id: "status", header: "Status", width: 110, cell: (m) => <Badge variant={STATUS_VARIANT[m.status] ?? "secondary"}>{m.status}</Badge> },
    { id: "confidence", header: "Confidence", width: 100, cell: (m) => m.confidence != null ? m.confidence.toFixed(2) : "—" },
    { id: "retrieved", header: "Retrieved", width: 90, cell: (m) => m.retrievalCount ?? 0 },
    { id: "ttl", header: "TTL expires", width: 170, cell: (m) => formatLocal(m.ttlExpiresAt) },
  ];

  return (
    <Can gate={FEATURE_GATES.browseMemory} fallback={<PermissionNotice />}>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm"><Brain className="size-4" aria-hidden />{t("memory.browse.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Scope</span>
              <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Scope" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
                {SCOPES.map((s) => <option key={s} value={s}>{s || "any"}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Scope ref (e.g. workspace id)</span>
              <Input value={scopeRef} onChange={(e) => setScopeRef(e.target.value)} aria-label="Scope ref" className="h-8 w-56 text-xs" />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Status</span>
              <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
                {STATUSES.map((s) => <option key={s} value={s}>{s || "any"}</option>)}
              </select>
            </label>
          </div>

          <div className="grid gap-3 lg:grid-cols-[1fr_320px]">
            <AsyncBoundary
              isLoading={query.isLoading}
              isError={query.isError}
              error={query.error}
              isEmpty={rows.length === 0}
              emptyTitle={t("memory.browse.empty")}
              onRetry={() => query.refetch()}
            >
              <DataTable
                ariaLabel={t("memory.browse.title")}
                rows={rows}
                columns={columns}
                rowId={(m) => m.id}
                onRowActivate={(m) => setSelected(m)}
                hasMore={query.hasNextPage}
                isFetchingMore={query.isFetchingNextPage}
                onLoadMore={() => query.fetchNextPage()}
              />
            </AsyncBoundary>
            <MemoryDetail id={selected?.id ?? null} onClose={() => setSelected(null)} />
          </div>
        </CardContent>
      </Card>
    </Can>
  );
}

function MemoryDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const query = useMemory(id);
  const m = query.data;

  if (!id) {
    return (
      <Card className="h-fit">
        <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
          <Brain className="size-6" aria-hidden />
          <p>Select a memory record to view it.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-fit">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">Memory record</CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {query.isLoading && <p className="text-muted-foreground">Loading…</p>}
        {m && (
          <>
            <p className="text-xs text-muted-foreground">
              {m.scope}/{m.scopeRef} · <Badge variant={STATUS_VARIANT[m.status] ?? "secondary"}>{m.status}</Badge>
            </p>
            {/* content is UNTRUSTED model input (BR-12) — rendered as plain text only. */}
            <p className="whitespace-pre-wrap rounded-md border bg-muted/30 p-2 text-xs">{m.content}</p>
            {m.tags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {m.tags.map((tg) => <Badge key={tg} variant="secondary">{tg}</Badge>)}
              </div>
            )}
            {m.mergedFrom && m.mergedFrom.length > 0 && (
              <p className="text-xs text-muted-foreground">merged from {m.mergedFrom.length} record(s)</p>
            )}
            <p className="text-xs text-muted-foreground">retrieved {m.retrievalCount ?? 0} time(s)</p>
            {m.ttlExpiresAt && <p className="text-xs text-muted-foreground">expires {formatLocal(m.ttlExpiresAt)}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ErasureCard() {
  const [subjectId, setSubjectId] = useState("");
  const [subjectType, setSubjectType] = useState("user");
  const [confirming, setConfirming] = useState(false);
  const [operationId, setOperationId] = useState<string | null>(null);
  const erase = useRequestMemoryErasure();
  const error = erase.error instanceof GraphQLRequestError ? erase.error : null;
  const status = useErasure(operationId, {
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "received" || s === "running" || s === "verifying" ? 2000 : false;
    },
  });

  return (
    <Can gate={FEATURE_GATES.requestMemoryErasure} fallback={<PermissionNotice compact />}>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm"><ShieldAlert className="size-4" aria-hidden />{t("memory.erasure.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-xs text-muted-foreground">{t("memory.erasure.description")}</p>
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (subjectId.trim()) setConfirming(true);
            }}
          >
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Subject type</span>
              <select value={subjectType} onChange={(e) => setSubjectType(e.target.value)} aria-label="Subject type" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
                <option value="user">user</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Subject id</span>
              <Input value={subjectId} onChange={(e) => setSubjectId(e.target.value)} aria-label="Subject id" className="h-8 text-xs" />
            </label>
            <Button type="submit" size="sm" variant="destructive" disabled={!subjectId.trim() || erase.isPending}>
              {t("memory.erasure.request")}
            </Button>
            {error && <p className="text-xs text-destructive">{error.message}</p>}
          </form>

          {operationId && (
            <div className="rounded-md border p-2 text-xs">
              <p>
                operation <span className="font-mono">{operationId}</span> ·{" "}
                <Badge variant={status.data?.status === "completed" ? "success" : status.data?.status === "failed" ? "destructive" : "warning"}>
                  {status.data?.status ?? "…"}
                </Badge>
              </p>
              {status.data?.completedAt && <p className="mt-1 text-muted-foreground">completed {formatLocal(status.data.completedAt)}</p>}
            </div>
          )}
        </CardContent>

        <ConfirmDialog
          open={confirming}
          onOpenChange={setConfirming}
          title={t("memory.erasure.confirmTitle")}
          description={t("memory.erasure.confirmDescription")}
          confirmLabel={t("memory.erasure.request")}
          confirmPhrase={subjectId.trim()}
          destructive
          onConfirm={() => {
            setConfirming(false);
            erase.mutate(
              { subjectId: subjectId.trim(), subjectType },
              { onSuccess: (r) => setOperationId(r.operationId) },
            );
          }}
        />
      </Card>
    </Can>
  );
}

function StatsCard() {
  const query = useMemoryStats();
  return (
    <Can gate={FEATURE_GATES.viewMemoryStats}>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{t("memory.stats.title")}</CardTitle>
        </CardHeader>
        <CardContent className="text-xs">
          {query.isLoading && <p className="text-muted-foreground">Loading…</p>}
          {query.isError && <p className="text-destructive">Failed to load stats.</p>}
          {query.data && (
            <dl className="space-y-1">
              {Object.entries(query.data as Record<string, unknown>).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="font-mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </CardContent>
      </Card>
    </Can>
  );
}

function PermissionNotice({ compact }: { compact?: boolean }) {
  return (
    <Card>
      <CardContent className={compact ? "py-3 text-xs text-muted-foreground" : "py-6 text-center text-sm text-muted-foreground"}>
        You don&apos;t have permission to view this section.
      </CardContent>
    </Card>
  );
}
