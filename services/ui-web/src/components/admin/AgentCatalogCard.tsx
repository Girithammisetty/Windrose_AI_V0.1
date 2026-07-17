"use client";
import { useState } from "react";
import { Bot, X } from "lucide-react";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { Badge, Card, CardHeader, CardTitle, CardDescription, CardContent, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useAgentDefinitions, useAgentVersions, usePublishAgentVersion,
  useTenantAgentConfig, usePutTenantAgentConfig,
} from "@/lib/graphql/hooks";
import type { AgentDefinition, AgentVersionInfo, TenantAgentConfig } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

/**
 * Agent catalog browse + per-tenant config (Tier 2b, agent-runtime registry
 * via the bff). NB: agent-runtime authorizes these routes on raw JWT scopes
 * (operator / tenant.admin), not rbac capabilities — the whole card is gated
 * on the Admin role by the caller (/admin route guard + FEATURE_GATES).
 */
export function AgentCatalogCard() {
  const query = useAgentDefinitions();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const rows = query.data ?? [];
  const selected = rows.find((d) => d.agentKey === selectedKey) ?? null;

  const columns: Column<AgentDefinition>[] = [
    { id: "key", header: "Agent", cell: (d) => <span className="font-mono text-xs font-medium">{d.agentKey}</span> },
    { id: "name", header: "Name", cell: (d) => d.displayName },
    { id: "writeMode", header: "Write mode", width: 110, cell: (d) => d.defaultWriteMode ?? "—" },
    {
      id: "status", header: "Status", width: 100,
      cell: (d) => <Badge variant={d.status === "published" ? "default" : "warning"}>{d.status ?? "—"}</Badge>,
    },
    {
      id: "latest", header: "Published v", width: 100,
      cell: (d) => d.latestPublishedVersion ?? <span className="text-muted-foreground">none</span>,
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Bot className="size-4" aria-hidden /> {t("agentCatalog.title")}
        </CardTitle>
        <CardDescription>{t("agentCatalog.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 xl:grid-cols-[1fr_440px]">
          <AsyncBoundary
            isLoading={query.isLoading} isError={query.isError} error={query.error}
            isEmpty={rows.length === 0} emptyTitle={t("agentCatalog.empty")} onRetry={() => query.refetch()}
          >
            <DataTable
              ariaLabel={t("agentCatalog.title")}
              rows={rows}
              columns={columns}
              rowId={(d) => d.agentKey}
              onRowActivate={(d) => setSelectedKey(d.agentKey)}
            />
          </AsyncBoundary>
          <AgentDetail definition={selected} onClose={() => setSelectedKey(null)} />
        </div>
      </CardContent>
    </Card>
  );
}

function AgentDetail({ definition, onClose }: { definition: AgentDefinition | null; onClose: () => void }) {
  const versions = useAgentVersions(definition?.agentKey ?? null);
  const config = useTenantAgentConfig(definition?.agentKey ?? null);
  const publish = usePublishAgentVersion();
  const [confirmPublish, setConfirmPublish] = useState<AgentVersionInfo | null>(null);

  if (!definition) {
    return (
      <Card className="h-fit">
        <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
          <Bot className="size-6" aria-hidden />
          <p>Select an agent to see its versions and this tenant&apos;s configuration.</p>
        </CardContent>
      </Card>
    );
  }

  const versionColumns: Column<AgentVersionInfo>[] = [
    { id: "version", header: "v", width: 50, cell: (v) => v.version },
    {
      id: "status", header: "Status", width: 110,
      cell: (v) => <Badge variant={v.status === "published" ? "default" : "warning"}>{v.status}</Badge>,
    },
    {
      id: "evalGate", header: "Eval gate", width: 120,
      cell: (v) => v.evalGateResultId ? <span className="font-mono text-[11px]">{v.evalGateResultId.slice(0, 10)}…</span> : <span className="text-muted-foreground">none</span>,
    },
    { id: "graph", header: "Graph", cell: (v) => <span className="font-mono text-[11px]">{v.graphRef ?? "—"}</span> },
    {
      id: "actions", header: "", width: 100,
      cell: (v) =>
        v.status !== "published" ? (
          <Can gate={FEATURE_GATES.publishAgentVersion}>
            <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setConfirmPublish(v); }}>
              {t("agentCatalog.publish")}
            </Button>
          </Can>
        ) : null,
    },
  ];

  return (
    <Card className="h-fit">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">{definition.displayName}</CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">{definition.description}</p>

        <p className="text-xs font-medium text-muted-foreground">{t("agentCatalog.versions")}</p>
        <AsyncBoundary
          isLoading={versions.isLoading} isError={versions.isError} error={versions.error}
          isEmpty={(versions.data ?? []).length === 0} emptyTitle="No versions."
          onRetry={() => versions.refetch()}
        >
          <DataTable
            ariaLabel={t("agentCatalog.versions")}
            rows={versions.data ?? []}
            columns={versionColumns}
            rowId={(v) => String(v.version)}
          />
        </AsyncBoundary>
        {publish.error && <p className="text-xs text-destructive">{publish.error.message}</p>}

        <p className="text-xs font-medium text-muted-foreground">{t("agentCatalog.tenantConfig")}</p>
        <AsyncBoundary
          isLoading={config.isLoading} isError={config.isError} error={config.error}
          isEmpty={!config.data} emptyTitle="No config." onRetry={() => config.refetch()}
        >
          {config.data && <TenantConfigForm key={definition.agentKey} config={config.data} />}
        </AsyncBoundary>
      </CardContent>

      <ConfirmDialog
        open={confirmPublish !== null}
        onOpenChange={(o) => !o && setConfirmPublish(null)}
        title={t("agentCatalog.confirmPublish.title")}
        description={t("agentCatalog.confirmPublish.description")}
        confirmLabel={t("agentCatalog.publish")}
        onConfirm={() => {
          if (confirmPublish) publish.mutate({ agentKey: definition.agentKey, version: confirmPublish.version });
          setConfirmPublish(null);
        }}
      />
    </Card>
  );
}

function TenantConfigForm({ config }: { config: TenantAgentConfig }) {
  const put = usePutTenantAgentConfig();
  const [enabled, setEnabled] = useState(config.enabled);
  const [selfApproval, setSelfApproval] = useState(config.selfApproval);
  const [pinnedVersion, setPinnedVersion] = useState(config.pinnedVersion != null ? String(config.pinnedVersion) : "");

  return (
    <form
      className="space-y-2 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        put.mutate({
          agentKey: config.agentKey,
          input: {
            enabled,
            selfApproval,
            pinnedVersion: pinnedVersion.trim() ? Number(pinnedVersion) : null,
          },
        });
      }}
    >
      {!config.configured && (
        <p className="text-xs text-muted-foreground">Not explicitly configured — values below are the runtime defaults.</p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex items-center gap-1.5 text-xs">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} aria-label={t("agentCatalog.enabled")} />
          <span>{t("agentCatalog.enabled")}</span>
        </label>
        <label className="flex items-center gap-1.5 text-xs">
          <input type="checkbox" checked={selfApproval} onChange={(e) => setSelfApproval(e.target.checked)} aria-label={t("agentCatalog.selfApproval")} />
          <span>{t("agentCatalog.selfApproval")}</span>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">{t("agentCatalog.pinnedVersion")}</span>
          <Input
            type="number" min="1" value={pinnedVersion}
            onChange={(e) => setPinnedVersion(e.target.value)}
            aria-label={t("agentCatalog.pinnedVersion")} className="h-8 w-24 text-xs"
          />
        </label>
        <Can gate={FEATURE_GATES.manageTenantAgentConfig}>
          <Button type="submit" size="sm" disabled={put.isPending}>{t("agentCatalog.saveConfig")}</Button>
        </Can>
      </div>
      {put.isSuccess && !put.isPending && <p className="text-xs text-muted-foreground">Saved.</p>}
      {put.error && <p className="text-xs text-destructive">{put.error.message}</p>}
    </form>
  );
}
