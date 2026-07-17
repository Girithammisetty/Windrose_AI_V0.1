"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Card, CardContent, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useAgentRunsList } from "@/lib/graphql/hooks";
import type { AgentRunListItem } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/**
 * Agent run history (Tier 2b): the tenant's runs from agent-runtime GET /runs
 * via the bff `agentRuns` — newest first, filterable by agent. Activating a
 * row opens the run's trace view (/copilot/runs/{id}). The open-by-id box
 * remains for deep links pasted from provenance badges / citations.
 */
export default function AgentRunsIndex() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [agentKey, setAgentKey] = useState("");
  const filters = useMemo(() => (agentKey ? { agentKey } : {}), [agentKey]);
  const query = useAgentRunsList(filters);
  const rows = query.data?.nodes ?? [];

  function open(e: React.FormEvent) {
    e.preventDefault();
    const id = value.trim().split(":").pop()?.split("/").pop();
    if (id) router.push(`/copilot/runs/${id}`);
  }

  const columns: Column<AgentRunListItem>[] = [
    { id: "agent", header: "Agent", width: 170, cell: (r) => <span className="font-mono text-xs font-medium">{r.agentKey ?? "—"}</span> },
    { id: "version", header: "v", width: 50, cell: (r) => r.agentVersion ?? "—" },
    {
      id: "status", header: "Status", width: 130,
      cell: (r) => (r.status ? <StatusChip status={String(r.status)} /> : <span className="text-muted-foreground">—</span>),
    },
    { id: "principal", header: "Principal", width: 140, cell: (r) => <span className="text-xs">{r.principalType ?? "—"}</span> },
    {
      id: "tokens", header: "Tokens", width: 120,
      cell: (r) => {
        const u = (r.usage ?? {}) as { input_tokens?: number; output_tokens?: number };
        return u.input_tokens != null || u.output_tokens != null
          ? <span className="font-mono text-xs">{u.input_tokens ?? 0}→{u.output_tokens ?? 0}</span>
          : <span className="text-muted-foreground">—</span>;
      },
    },
    { id: "created", header: "Started", width: 170, cell: (r) => formatLocal(r.createdAt) },
    { id: "id", header: "Run id", cell: (r) => <span className="font-mono text-[11px] text-muted-foreground">{r.id}</span> },
  ];

  return (
    <div>
      <PageHeader title={t("agentRuns.title")} description={t("agentRuns.subtitle")} />

      <Card className="mb-4">
        <CardContent className="pt-4">
          <form onSubmit={open} className="flex flex-wrap items-center gap-2">
            <Input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="run id or wr:tenant:agent_run:agent_run/..."
              aria-label={t("agentRuns.openById")}
              className="max-w-md"
            />
            <Button type="submit" variant="outline">Open trace</Button>
            <label className="ml-auto flex items-center gap-1 text-sm">
              <span className="text-muted-foreground">Agent</span>
              <Input
                value={agentKey}
                onChange={(e) => setAgentKey(e.target.value)}
                placeholder="filter by agent key"
                aria-label="Filter runs by agent key"
                className="h-9 w-48 text-sm"
              />
            </label>
          </form>
        </CardContent>
      </Card>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle={t("agentRuns.empty")}
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel={t("agentRuns.title")}
          rows={rows}
          columns={columns}
          rowId={(r) => r.id}
          onRowActivate={(r) => router.push(`/copilot/runs/${r.id}`)}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Bot className="size-8" />
              <p>{t("agentRuns.empty")}</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}
