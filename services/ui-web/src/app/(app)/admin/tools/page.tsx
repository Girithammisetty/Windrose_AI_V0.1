"use client";
import { useMemo, useState } from "react";
import { Wrench, PackageCheck, X, Inbox as InboxIcon } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardDescription, CardContent, Input, Textarea } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useTools, useRegisterTool, useToolHealth, usePublishToolVersion, useDeprecateToolVersion,
  useRetireToolVersion, useSetToolEnablement, useByoSubmissions, useSubmitByoTool, useDecideByoTool,
} from "@/lib/graphql/hooks";
import type { Tool, ToolVersionHealth, ByoSubmission } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";
import { formatLocal } from "@/lib/utils";

/**
 * Tool registry admin (Tier 2b, tool-plane via the bff): catalog + version
 * lifecycle (publish/deprecate/retire, TPL-FR-001/002/003), per-tenant
 * enablement (TPL-FR-004), and the BYO onboarding queue (TPL-FR-040).
 * Kill switches stay on /admin/agents (Tier 1) — this page is the catalog
 * control plane, not the emergency stop.
 */
export default function AdminToolsPage() {
  return (
    <div>
      <PageHeader title={t("toolsAdmin.title")} description={t("toolsAdmin.subtitle")} />
      <div className="space-y-4">
        <CatalogCard />
        <ByoQueueCard />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog + per-tool detail (versions/health/lifecycle/enablement)
// ---------------------------------------------------------------------------
function CatalogCard() {
  const query = useTools();
  const register = useRegisterTool();
  const [registering, setRegistering] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const selected = rows.find((x) => x.toolId === selectedId) ?? null;

  const columns: Column<Tool>[] = [
    { id: "toolId", header: "Tool", cell: (x) => <span className="font-mono text-xs font-medium">{x.toolId}</span> },
    { id: "name", header: "Name", cell: (x) => x.displayName ?? "—" },
    { id: "owner", header: "Owner service", width: 160, cell: (x) => <span className="font-mono text-xs">{x.ownerService}</span> },
    {
      id: "sideEffects", header: "Side effects", width: 120,
      cell: (x) => <Badge variant={x.sideEffects === "destructive" ? "destructive" : "default"}>{x.sideEffects}</Badge>,
    },
    {
      id: "default", header: "Default", width: 90,
      cell: (x) => (x.enabledByDefault ? "enabled" : "disabled"),
    },
    { id: "created", header: "Registered", width: 160, cell: (x) => formatLocal(x.createdAt) },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Wrench className="size-4" aria-hidden /> {t("toolsAdmin.catalog.title")}
        </CardTitle>
        <Can gate={FEATURE_GATES.registerTool}>
          <Button size="sm" onClick={() => setRegistering((v) => !v)}>
            {registering ? t("action.cancel") : t("toolsAdmin.register")}
          </Button>
        </Can>
      </CardHeader>
      <CardContent className="space-y-3">
        {registering && (
          <RegisterToolForm
            pending={register.isPending}
            error={register.error}
            onRegister={(input) => register.mutate(input, { onSuccess: () => setRegistering(false) })}
          />
        )}
        <div className="grid gap-3 xl:grid-cols-[1fr_440px]">
          <AsyncBoundary
            isLoading={query.isLoading} isError={query.isError} error={query.error}
            isEmpty={rows.length === 0} emptyTitle={t("toolsAdmin.catalog.empty")} onRetry={() => query.refetch()}
          >
            <DataTable
              ariaLabel={t("toolsAdmin.catalog.title")}
              rows={rows}
              columns={columns}
              rowId={(x) => x.toolId}
              hasMore={query.hasNextPage}
              isFetchingMore={query.isFetchingNextPage}
              onLoadMore={() => query.fetchNextPage()}
              onRowActivate={(x) => setSelectedId(x.toolId)}
            />
          </AsyncBoundary>
          <ToolDetail tool={selected} onClose={() => setSelectedId(null)} />
        </div>
      </CardContent>
    </Card>
  );
}

function RegisterToolForm({
  onRegister, pending, error,
}: {
  onRegister: (input: { toolId: string; displayName?: string; ownerService: string; ownerTeam?: string; sideEffects?: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [toolId, setToolId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [ownerService, setOwnerService] = useState("");
  const [sideEffects, setSideEffects] = useState("none");

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!toolId.trim() || !ownerService.trim()) return;
        onRegister({ toolId: toolId.trim(), displayName: displayName.trim() || undefined, ownerService: ownerService.trim(), sideEffects });
      }}
    >
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Tool id (namespaced, e.g. case.assign)</span>
        <Input value={toolId} onChange={(e) => setToolId(e.target.value)} aria-label="Tool id" className="h-8 w-48 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Display name</span>
        <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} aria-label="Tool display name" className="h-8 w-44 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Owner service</span>
        <Input value={ownerService} onChange={(e) => setOwnerService(e.target.value)} placeholder="case-service" aria-label="Owner service" className="h-8 w-40 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Side effects</span>
        <select value={sideEffects} onChange={(e) => setSideEffects(e.target.value)} aria-label="Side effects" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
          <option value="none">none</option>
          <option value="reversible">reversible</option>
          <option value="destructive">destructive</option>
        </select>
      </label>
      <Button type="submit" size="sm" disabled={pending}>{t("toolsAdmin.register")}</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}
    </form>
  );
}

function ToolDetail({ tool, onClose }: { tool: Tool | null; onClose: () => void }) {
  const health = useToolHealth(tool?.toolId ?? null);
  const publish = usePublishToolVersion();
  const deprecate = useDeprecateToolVersion();
  const retire = useRetireToolVersion();
  const enablement = useSetToolEnablement();
  const [confirm, setConfirm] = useState<{ kind: "deprecate" | "retire"; version: string } | null>(null);
  const [confirmDisable, setConfirmDisable] = useState(false);
  const [retireReason, setRetireReason] = useState("");

  if (!tool) {
    return (
      <Card className="h-fit">
        <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
          <PackageCheck className="size-6" aria-hidden />
          <p>Select a tool to manage its versions, health, and tenant enablement.</p>
        </CardContent>
      </Card>
    );
  }

  const lifecycleError = publish.error ?? deprecate.error ?? retire.error;

  const versionColumns: Column<ToolVersionHealth>[] = [
    { id: "version", header: "Version", width: 90, cell: (v) => <span className="font-mono text-xs">{v.version}</span> },
    {
      id: "status", header: "Status", width: 110,
      cell: (v) => (
        <Badge variant={v.status === "published" ? "default" : v.status === "retired" ? "destructive" : "warning"}>
          {v.status}
        </Badge>
      ),
    },
    {
      id: "health", header: "Health", cell: (v) =>
        v.health ? <span className="font-mono text-[11px]">{JSON.stringify(v.health)}</span> : <span className="text-muted-foreground">no traffic</span>,
    },
    {
      id: "actions", header: "", width: 210,
      cell: (v) => (
        <span className="flex gap-1">
          {v.status === "draft" && (
            <Can gate={FEATURE_GATES.updateToolVersion}>
              <Button
                variant="ghost" size="sm" disabled={publish.isPending}
                onClick={(e) => { e.stopPropagation(); publish.mutate({ toolId: tool.toolId, version: v.version }); }}
              >
                {t("toolsAdmin.versions.publish")}
              </Button>
            </Can>
          )}
          {v.status === "published" && (
            <Can gate={FEATURE_GATES.updateToolVersion}>
              <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setConfirm({ kind: "deprecate", version: v.version }); }}>
                {t("toolsAdmin.versions.deprecate")}
              </Button>
            </Can>
          )}
          {(v.status === "deprecated" || v.status === "draft") && (
            <Can gate={FEATURE_GATES.retireToolVersion}>
              <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setConfirm({ kind: "retire", version: v.version }); }}>
                {t("toolsAdmin.versions.retire")}
              </Button>
            </Can>
          )}
        </span>
      ),
    },
  ];

  return (
    <Card className="h-fit">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="font-mono text-xs">{tool.toolId}</CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">
          {tool.displayName ?? tool.toolId} · owned by {tool.ownerService} · side effects {tool.sideEffects}
        </p>

        <p className="text-xs font-medium text-muted-foreground">{t("toolsAdmin.versions.title")}</p>
        <AsyncBoundary
          isLoading={health.isLoading} isError={health.isError} error={health.error}
          isEmpty={(health.data?.versions ?? []).length === 0} emptyTitle="No versions yet."
          onRetry={() => health.refetch()}
        >
          <DataTable
            ariaLabel={t("toolsAdmin.versions.title")}
            rows={health.data?.versions ?? []}
            columns={versionColumns}
            rowId={(v) => v.version}
          />
        </AsyncBoundary>
        {lifecycleError && <p className="text-xs text-destructive">{lifecycleError.message}</p>}

        <p className="text-xs font-medium text-muted-foreground">{t("toolsAdmin.enablement.title")}</p>
        <Can gate={FEATURE_GATES.setToolEnablement}>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm" variant="outline" disabled={enablement.isPending}
              onClick={() => enablement.mutate({ toolId: tool.toolId, input: { enabled: true } })}
            >
              {t("toolsAdmin.enablement.enable")}
            </Button>
            <Button size="sm" variant="destructive" disabled={enablement.isPending} onClick={() => setConfirmDisable(true)}>
              {t("toolsAdmin.enablement.disable")}
            </Button>
            {enablement.data && (
              <span className="text-xs text-muted-foreground" data-testid="enablement-state">
                tenant: {enablement.data.enabled ? "enabled" : "disabled"} · {formatLocal(enablement.data.updatedAt)}
              </span>
            )}
          </div>
          {enablement.error && <p className="text-xs text-destructive">{enablement.error.message}</p>}
        </Can>
      </CardContent>

      <ConfirmDialog
        open={confirm?.kind === "deprecate"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t("toolsAdmin.versions.confirmDeprecate.title")}
        description={t("toolsAdmin.versions.confirmDeprecate.description")}
        confirmLabel={t("toolsAdmin.versions.deprecate")}
        onConfirm={() => {
          if (confirm) deprecate.mutate({ toolId: tool.toolId, version: confirm.version });
          setConfirm(null);
        }}
      />
      <ConfirmDialog
        open={confirm?.kind === "retire"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={t("toolsAdmin.versions.confirmRetire.title")}
        description={t("toolsAdmin.versions.confirmRetire.description")}
        confirmLabel={t("toolsAdmin.versions.retire")}
        confirmPhrase={confirm ? `${tool.toolId}@${confirm.version}` : undefined}
        destructive
        onConfirm={() => {
          if (confirm) retire.mutate({ toolId: tool.toolId, version: confirm.version, force: true, reason: retireReason || "retired via admin UI" });
          setConfirm(null);
          setRetireReason("");
        }}
      >
        <label className="mt-3 flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Reason (required when the deprecation window has not elapsed)</span>
          <Input value={retireReason} onChange={(e) => setRetireReason(e.target.value)} aria-label="Retire reason" className="h-8 text-xs" />
        </label>
      </ConfirmDialog>
      <ConfirmDialog
        open={confirmDisable}
        onOpenChange={setConfirmDisable}
        title={t("toolsAdmin.enablement.confirmDisable.title")}
        description={t("toolsAdmin.enablement.confirmDisable.description")}
        confirmLabel={t("toolsAdmin.enablement.disable")}
        destructive
        onConfirm={() => {
          setConfirmDisable(false);
          enablement.mutate({ toolId: tool.toolId, input: { enabled: false } });
        }}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// BYO onboarding queue
// ---------------------------------------------------------------------------
function ByoQueueCard() {
  const [status, setStatus] = useState("pending_approval");
  const query = useByoSubmissions(status || undefined);
  const submit = useSubmitByoTool();
  const decide = useDecideByoTool();
  const [submitting, setSubmitting] = useState(false);
  const [confirmApprove, setConfirmApprove] = useState<ByoSubmission | null>(null);

  const columns: Column<ByoSubmission>[] = [
    { id: "endpoint", header: "Endpoint", cell: (b) => <span className="font-mono text-xs">{b.endpointUrl}</span> },
    { id: "tier", header: "Requested tier", width: 130, cell: (b) => b.requestedTier },
    { id: "auth", header: "Auth", width: 90, cell: (b) => b.authMethod },
    {
      id: "status", header: "Status", width: 140,
      cell: (b) => (
        <Badge variant={b.status === "approved" ? "default" : b.status === "rejected" ? "destructive" : "warning"}>
          {b.status}
        </Badge>
      ),
    },
    { id: "created", header: "Submitted", width: 160, cell: (b) => formatLocal(b.createdAt) },
    {
      id: "actions", header: "", width: 170,
      cell: (b) =>
        b.status === "pending_approval" ? (
          <Can gate={FEATURE_GATES.decideByoTool}>
            <span className="flex gap-1">
              <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setConfirmApprove(b); }}>
                {t("toolsAdmin.byo.approve")}
              </Button>
              <Button
                variant="ghost" size="sm" disabled={decide.isPending}
                onClick={(e) => { e.stopPropagation(); decide.mutate({ id: b.id, decision: "reject", message: "rejected via admin UI" }); }}
              >
                {t("toolsAdmin.byo.reject")}
              </Button>
            </span>
          </Can>
        ) : null,
    },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2 text-sm">
            <InboxIcon className="size-4" aria-hidden /> {t("toolsAdmin.byo.title")}
          </CardTitle>
          <CardDescription>External tools awaiting review. Write-direct is forbidden for external tools.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter BYO submissions by status"
            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          >
            <option value="pending_approval">pending</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
            <option value="">all</option>
          </select>
          <Can gate={FEATURE_GATES.submitByoTool}>
            <Button size="sm" onClick={() => setSubmitting((v) => !v)}>
              {submitting ? t("action.cancel") : t("toolsAdmin.byo.submit")}
            </Button>
          </Can>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {submitting && (
          <SubmitByoForm
            pending={submit.isPending}
            error={submit.error}
            onSubmit={(input) => submit.mutate(input, { onSuccess: () => setSubmitting(false) })}
          />
        )}
        <AsyncBoundary
          isLoading={query.isLoading} isError={query.isError} error={query.error}
          isEmpty={(query.data ?? []).length === 0} emptyTitle={t("toolsAdmin.byo.empty")}
          onRetry={() => query.refetch()}
        >
          <DataTable ariaLabel={t("toolsAdmin.byo.title")} rows={query.data ?? []} columns={columns} rowId={(b) => b.id} />
        </AsyncBoundary>
        {decide.error && <p className="text-xs text-destructive">{decide.error.message}</p>}
      </CardContent>

      <ConfirmDialog
        open={confirmApprove !== null}
        onOpenChange={(o) => !o && setConfirmApprove(null)}
        title={t("toolsAdmin.byo.confirmApprove.title")}
        description={t("toolsAdmin.byo.confirmApprove.description")}
        confirmLabel={t("toolsAdmin.byo.approve")}
        onConfirm={() => {
          if (confirmApprove) decide.mutate({ id: confirmApprove.id, decision: "approve", message: "approved via admin UI" });
          setConfirmApprove(null);
        }}
      />
    </Card>
  );
}

function SubmitByoForm({
  onSubmit, pending, error,
}: {
  onSubmit: (input: { endpointUrl: string; requestedTier?: string; manifest?: unknown; egressDescription?: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [endpointUrl, setEndpointUrl] = useState("");
  const [tier, setTier] = useState("read");
  const [manifest, setManifest] = useState("{}");
  const [egress, setEgress] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  return (
    <form
      className="space-y-2 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!endpointUrl.trim()) return;
        let m: unknown;
        try {
          m = JSON.parse(manifest || "{}");
        } catch {
          setParseError("Manifest must be valid JSON.");
          return;
        }
        setParseError(null);
        onSubmit({ endpointUrl: endpointUrl.trim(), requestedTier: tier, manifest: m, egressDescription: egress });
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-72 flex-1 flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Endpoint URL</span>
          <Input value={endpointUrl} onChange={(e) => setEndpointUrl(e.target.value)} placeholder="https://tools.example.com/mcp" aria-label="BYO endpoint URL" className="h-8 text-xs" />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Requested tier (max write-proposal)</span>
          <select value={tier} onChange={(e) => setTier(e.target.value)} aria-label="Requested tier" className="h-8 rounded-md border border-input bg-background px-2 text-xs">
            <option value="read">read</option>
            <option value="write-proposal">write-proposal</option>
          </select>
        </label>
      </div>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Manifest (JSON)</span>
        <Textarea value={manifest} onChange={(e) => setManifest(e.target.value)} aria-label="BYO manifest" className="min-h-[60px] font-mono text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Data egress description</span>
        <Input value={egress} onChange={(e) => setEgress(e.target.value)} aria-label="Data egress description" className="h-8 text-xs" />
      </label>
      <Button type="submit" size="sm" disabled={pending}>{t("toolsAdmin.byo.submit")}</Button>
      {(parseError || error) && <p className="text-xs text-destructive">{parseError ?? error?.message}</p>}
    </form>
  );
}
