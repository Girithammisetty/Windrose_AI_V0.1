"use client";
import { useMemo, useState } from "react";
import { ScrollText } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Badge, Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { AuthzExplainPanel } from "@/components/admin/AuthzExplainPanel";
import { AuditComplianceCard } from "@/components/admin/AuditComplianceCard";
import { useAuditEvents } from "@/lib/graphql/hooks";
import type { AuditEvent, AuditEventsFilter } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";

/** RFC3339 for `n` days ago (audit enforces a 92-day max window). */
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

export default function AdminAuditPage() {
  const [eventType, setEventType] = useState("");
  const [actorType, setActorType] = useState("");
  const [actorId, setActorId] = useState("");
  const [days, setDays] = useState(7);

  const filter: AuditEventsFilter = useMemo(
    () => ({
      from: daysAgo(days),
      // `to` omitted → BFF defaults to now.
      eventType: eventType || undefined,
      actorType: actorType || undefined,
      actorId: actorId.trim() || undefined,
    }),
    [days, eventType, actorType, actorId],
  );

  const query = useAuditEvents(filter);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const columns: Column<AuditEvent>[] = [
    { id: "when", header: "When", width: 180, cell: (e) => <span className="whitespace-nowrap">{formatLocal(e.occurredAt)}</span> },
    { id: "type", header: "Event", width: 190, cell: (e) => <span className="font-medium">{e.eventType}</span> },
    {
      id: "actor", header: "Actor", width: 200,
      cell: (e) => (
        <span className="flex items-center gap-1">
          {e.actorType && <Badge variant="secondary">{e.actorType}</Badge>}
          <span className="truncate font-mono text-xs">{e.actorId ?? "—"}</span>
        </span>
      ),
    },
    {
      id: "via", header: "Via agent", width: 140,
      cell: (e) => e.viaAgentId ? <span className="truncate font-mono text-xs">{e.viaAgentId}</span> : <span className="text-muted-foreground">—</span>,
    },
    { id: "action", header: "Action", width: 170, cell: (e) => e.action || <span className="text-muted-foreground">—</span> },
    { id: "resource", header: "Resource", cell: (e) => <span className="truncate font-mono text-xs">{e.resourceUrn ?? "—"}</span> },
    {
      id: "chain", header: "Seq", width: 80,
      cell: (e) => e.chainSeq != null ? <span className="font-mono text-xs">#{e.chainSeq}</span> : <span className="text-muted-foreground">—</span>,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Audit search"
        description="The tamper-evident WORM compliance trail (audit-service, ClickHouse). Dual-attribution: agent actions carry the on-behalf-of user."
      />

      <AuditComplianceCard />

      <div className="mb-4">
        <AuthzExplainPanel />
      </div>

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Window</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time window"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm">
            <option value={1}>Last 24h</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Actor type</span>
          <select value={actorType} onChange={(e) => setActorType(e.target.value)} aria-label="Actor type"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm">
            <option value="">any</option>
            <option value="user">user</option>
            <option value="service">service</option>
            <option value="agent">agent</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Event type</span>
          <Input value={eventType} onChange={(e) => setEventType(e.target.value)} placeholder="e.g. agent_run" className="h-9 w-44" aria-label="Event type" />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">Actor id</span>
          <Input value={actorId} onChange={(e) => setActorId(e.target.value)} placeholder="exact id" className="h-9 w-44" aria-label="Actor id" />
        </label>
        {(eventType || actorType || actorId) && (
          <Button variant="ghost" size="sm" onClick={() => { setEventType(""); setActorType(""); setActorId(""); }}>Clear</Button>
        )}
      </div>

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No audit events match these filters."
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Audit events"
          rows={rows}
          columns={columns}
          rowId={(e) => e.eventId}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <ScrollText className="size-8" />
              <p>No audit events in this window.</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}
