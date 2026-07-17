"use client";
import { useState } from "react";
import { Siren, Bot, Wrench, X } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { Can } from "@/components/authz/Can";
import { AgentCatalogCard } from "@/components/admin/AgentCatalogCard";
import { Badge, Card, CardHeader, CardTitle, CardContent, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES, type Gate } from "@/lib/authz/registry";
import {
  useAgentKillSwitches, useCreateAgentKillSwitch, useDeleteAgentKillSwitch,
  useToolKillSwitches, useCreateToolKillSwitch, useDeleteToolKillSwitch,
} from "@/lib/graphql/hooks";
import type { KillSwitch } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";
import { formatLocal } from "@/lib/utils";

const AGENT_SCOPES = ["agent_version_tenant", "agent_version", "agent"];
const TOOL_SCOPES = ["tool_tenant", "tool_version", "tool"];

export default function AdminAgentsPage() {
  return (
    <div>
      <PageHeader title={t("killSwitch.title")} description={t("killSwitch.subtitle")} />
      <div className="grid gap-4 lg:grid-cols-2">
        <AgentKillSwitchesCard />
        <ToolKillSwitchesCard />
      </div>
      {/* Tier 2b: agent catalog browse + per-tenant agent config (agent-runtime
          registry). Lives with the kill switches — one agent control plane page. */}
      <div className="mt-4">
        <AgentCatalogCard />
      </div>
    </div>
  );
}

function killSwitchColumns(kind: "agent" | "tool"): Column<KillSwitch>[] {
  return [
    {
      id: "target", header: kind === "agent" ? "Agent" : "Tool",
      cell: (k) => <span className="font-medium">{kind === "agent" ? k.agentKey : k.toolId}</span>,
    },
    { id: "scope", header: "Scope", width: 170, cell: (k) => <span className="font-mono text-xs">{k.scope}</span> },
    { id: "version", header: "Version", width: 90, cell: (k) => k.version ?? <span className="text-muted-foreground">—</span> },
    {
      id: "tenant", header: "Tenant", width: 100,
      cell: (k) => k.tenantId ? <span className="font-mono text-xs">{k.tenantId.slice(0, 8)}…</span> : <Badge variant="warning">global</Badge>,
    },
    { id: "reason", header: "Reason", cell: (k) => k.reason },
    { id: "setBy", header: "Set by", width: 140, cell: (k) => k.setBy },
    { id: "createdAt", header: "Since", width: 170, cell: (k) => formatLocal(k.createdAt) },
  ];
}

function AgentKillSwitchesCard() {
  const query = useAgentKillSwitches();
  const create = useCreateAgentKillSwitch();
  const del = useDeleteAgentKillSwitch();
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<KillSwitch | null>(null);
  const rows = query.data ?? [];
  const columns = killSwitchColumns("agent");

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm"><Bot className="size-4" aria-hidden />{t("killSwitch.agents.title")}</CardTitle>
        <Can gate={FEATURE_GATES.createAgentKillSwitch}>
          <Button size="sm" variant="destructive" onClick={() => setCreating((v) => !v)}>
            {creating ? t("action.cancel") : t("killSwitch.new")}
          </Button>
        </Can>
      </CardHeader>
      <CardContent className="space-y-3">
        {creating && (
          <NewAgentKillForm
            pending={create.isPending}
            error={create.error}
            onCreate={(input) =>
              create.mutate(input, { onSuccess: (r) => { setCreating(false); setSelected(r.createAgentKillSwitch); } })
            }
          />
        )}
        <div className="grid gap-3 lg:grid-cols-[1fr_280px]">
          <AsyncBoundary
            isLoading={query.isLoading}
            isError={query.isError}
            error={query.error}
            isEmpty={rows.length === 0}
            emptyTitle={t("killSwitch.empty")}
            onRetry={() => query.refetch()}
          >
            <DataTable
              ariaLabel={t("killSwitch.agents.title")}
              rows={rows}
              columns={columns}
              rowId={(k) => k.id}
              onRowActivate={(k) => setSelected(k)}
            />
          </AsyncBoundary>
          <KillSwitchDetail
            killSwitch={selected}
            liftGate={FEATURE_GATES.liftAgentKillSwitch}
            pending={del.isPending}
            onClose={() => setSelected(null)}
            onLift={() => del.mutate(selected!.id, { onSuccess: () => setSelected(null) })}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function KillSwitchDetail({
  killSwitch,
  liftGate,
  pending,
  onClose,
  onLift,
}: {
  killSwitch: KillSwitch | null;
  liftGate: Gate;
  pending: boolean;
  onClose: () => void;
  onLift: () => void;
}) {
  const [confirmingLift, setConfirmingLift] = useState(false);

  if (!killSwitch) {
    return (
      <Card className="h-fit">
        <CardContent className="flex flex-col items-center gap-2 py-6 text-center text-sm text-muted-foreground">
          <Siren className="size-6" aria-hidden />
          <p>Select a kill switch to view or lift it.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-fit">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">{killSwitch.agentKey ?? killSwitch.toolId}</CardTitle>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close"><X className="size-4" /></Button>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="text-xs text-muted-foreground">
          scope <span className="font-mono">{killSwitch.scope}</span>
          {killSwitch.version ? <> · version {killSwitch.version}</> : null}
          {" · "}{killSwitch.tenantId ? `tenant ${killSwitch.tenantId.slice(0, 8)}…` : "platform-wide"}
        </p>
        <p>{killSwitch.reason}</p>
        <p className="text-xs text-muted-foreground">set by {killSwitch.setBy} · {formatLocal(killSwitch.createdAt)}</p>
        <Can gate={liftGate}>
          <Button size="sm" variant="destructive" disabled={pending} onClick={() => setConfirmingLift(true)}>
            {t("killSwitch.lift")}
          </Button>
        </Can>
      </CardContent>

      <ConfirmDialog
        open={confirmingLift}
        onOpenChange={setConfirmingLift}
        title={t("killSwitch.confirmLift.title")}
        description={t("killSwitch.confirmLift.description")}
        confirmLabel={t("killSwitch.lift")}
        destructive
        onConfirm={() => { setConfirmingLift(false); onLift(); }}
      />
    </Card>
  );
}

function NewAgentKillForm({
  onCreate,
  pending,
  error,
}: {
  onCreate: (input: { agentKey: string; scope: string; version?: number; reason: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [agentKey, setAgentKey] = useState("");
  const [scope, setScope] = useState(AGENT_SCOPES[0]);
  const [version, setVersion] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);

  const submit = () => {
    if (!agentKey.trim() || !reason.trim()) return;
    onCreate({
      agentKey: agentKey.trim(), scope,
      version: version.trim() ? Number(version) : undefined,
      reason: reason.trim(),
    });
  };

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3"
      onSubmit={(e) => { e.preventDefault(); if (agentKey.trim() && reason.trim()) setConfirming(true); }}
    >
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">{t("killSwitch.field.agentKey")}</span>
        <Input value={agentKey} onChange={(e) => setAgentKey(e.target.value)} aria-label={t("killSwitch.field.agentKey")} className="h-8 w-40 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">{t("killSwitch.field.scope")}</span>
        <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label={t("killSwitch.field.scope")} className="h-8 rounded-md border border-input bg-background px-2 text-xs">
          {AGENT_SCOPES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Version</span>
        <Input type="number" min="1" value={version} onChange={(e) => setVersion(e.target.value)} aria-label="Version" className="h-8 w-20 text-xs" />
      </label>
      <label className="flex flex-1 flex-col gap-1 text-xs">
        <span className="text-muted-foreground">{t("killSwitch.field.reason")}</span>
        <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("killSwitch.field.reasonPlaceholder")} aria-label={t("killSwitch.field.reason")} className="h-8 min-w-48 text-xs" />
      </label>
      <Button type="submit" size="sm" variant="destructive" disabled={pending}>{t("killSwitch.new")}</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t("killSwitch.confirmCreate.title")}
        description={t("killSwitch.confirmCreate.description")}
        confirmLabel={t("killSwitch.new")}
        confirmPhrase={agentKey.trim()}
        destructive
        onConfirm={() => { setConfirming(false); submit(); }}
      >
        <div className="mt-3 space-y-1 text-xs text-muted-foreground">
          <p><Siren className="mr-1 inline size-3" aria-hidden />scope <span className="font-mono">{scope}</span> · reason &ldquo;{reason}&rdquo;</p>
        </div>
      </ConfirmDialog>
    </form>
  );
}

function ToolKillSwitchesCard() {
  const query = useToolKillSwitches();
  const create = useCreateToolKillSwitch();
  const del = useDeleteToolKillSwitch();
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<KillSwitch | null>(null);
  const rows = query.data ?? [];
  const columns = killSwitchColumns("tool");

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2 text-sm"><Wrench className="size-4" aria-hidden />{t("killSwitch.tools.title")}</CardTitle>
        <Can gate={FEATURE_GATES.createToolKillSwitch}>
          <Button size="sm" variant="destructive" onClick={() => setCreating((v) => !v)}>
            {creating ? t("action.cancel") : t("killSwitch.new")}
          </Button>
        </Can>
      </CardHeader>
      <CardContent className="space-y-3">
        {creating && (
          <NewToolKillForm
            pending={create.isPending}
            error={create.error}
            onCreate={(input) =>
              create.mutate(input, { onSuccess: (r) => { setCreating(false); setSelected(r.createToolKillSwitch); } })
            }
          />
        )}
        <div className="grid gap-3 lg:grid-cols-[1fr_280px]">
          <AsyncBoundary
            isLoading={query.isLoading}
            isError={query.isError}
            error={query.error}
            isEmpty={rows.length === 0}
            emptyTitle={t("killSwitch.empty")}
            onRetry={() => query.refetch()}
          >
            <DataTable
              ariaLabel={t("killSwitch.tools.title")}
              rows={rows}
              columns={columns}
              rowId={(k) => k.id}
              onRowActivate={(k) => setSelected(k)}
            />
          </AsyncBoundary>
          <KillSwitchDetail
            killSwitch={selected}
            liftGate={FEATURE_GATES.liftToolKillSwitch}
            pending={del.isPending}
            onClose={() => setSelected(null)}
            onLift={() => del.mutate(selected!.id, { onSuccess: () => setSelected(null) })}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function NewToolKillForm({
  onCreate,
  pending,
  error,
}: {
  onCreate: (input: { toolId: string; scope: string; version?: string; reason: string }) => void;
  pending: boolean;
  error: Error | null;
}) {
  const [toolId, setToolId] = useState("");
  const [scope, setScope] = useState(TOOL_SCOPES[0]);
  const [version, setVersion] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);

  const submit = () => {
    if (!toolId.trim() || !reason.trim()) return;
    onCreate({ toolId: toolId.trim(), scope, version: version.trim() || undefined, reason: reason.trim() });
  };

  return (
    <form
      className="flex flex-wrap items-end gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3"
      onSubmit={(e) => { e.preventDefault(); if (toolId.trim() && reason.trim()) setConfirming(true); }}
    >
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">{t("killSwitch.field.toolId")}</span>
        <Input value={toolId} onChange={(e) => setToolId(e.target.value)} aria-label={t("killSwitch.field.toolId")} className="h-8 w-40 text-xs" />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">{t("killSwitch.field.scope")}</span>
        <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label={t("killSwitch.field.scope")} className="h-8 rounded-md border border-input bg-background px-2 text-xs">
          {TOOL_SCOPES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">Version</span>
        <Input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="1.0.0" aria-label="Version" className="h-8 w-24 text-xs" />
      </label>
      <label className="flex flex-1 flex-col gap-1 text-xs">
        <span className="text-muted-foreground">{t("killSwitch.field.reason")}</span>
        <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("killSwitch.field.reasonPlaceholder")} aria-label={t("killSwitch.field.reason")} className="h-8 min-w-48 text-xs" />
      </label>
      <Button type="submit" size="sm" variant="destructive" disabled={pending}>{t("killSwitch.new")}</Button>
      {error && <p className="w-full text-xs text-destructive">{error.message}</p>}

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t("killSwitch.confirmCreate.title")}
        description={t("killSwitch.confirmCreate.description")}
        confirmLabel={t("killSwitch.new")}
        confirmPhrase={toolId.trim()}
        destructive
        onConfirm={() => { setConfirming(false); submit(); }}
      >
        <div className="mt-3 space-y-1 text-xs text-muted-foreground">
          <p><Siren className="mr-1 inline size-3" aria-hidden />scope <span className="font-mono">{scope}</span> · reason &ldquo;{reason}&rdquo;</p>
        </div>
      </ConfirmDialog>
    </form>
  );
}
