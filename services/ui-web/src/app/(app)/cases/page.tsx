"use client";
import { useMemo } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Briefcase, Settings } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Input } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCaseSearch } from "@/lib/graphql/hooks";
import { useSelection } from "@/stores/ui";
import { BulkAssignBar } from "@/components/cases/BulkAssignBar";
import { CaseExportButton } from "@/components/cases/CaseExportButton";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { FEATURE_GATES } from "@/lib/authz/registry";
import type { Case } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

const STATUSES = ["", "DRAFT", "UNASSIGNED", "IN_PROGRESS", "RESOLVED", "CLOSED"];
const SEVERITIES = ["", "LOW", "MEDIUM", "HIGH", "CRITICAL"];

export default function CasesPage() {
  const router = useRouter();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const status = params.get("status") ?? "";
  const severity = params.get("severity") ?? "";

  // URL is source of truth for shareable view state (UI-FR-043).
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`/cases?${next.toString()}`);
  };

  const filter = useMemo(
    () => ({ status: status || undefined, severity: severity || undefined }),
    [status, severity],
  );
  const query = useCaseSearch({ q: q || undefined, filter });
  // Task #78: this was a list-wide "any case" subscription, but realtime-hub
  // only routes to a single resource topic (run-status:<case-urn>) — there is
  // no "all cases in my tenant" scheme, so this always 422'd. Removed; the
  // detail page (cases/[id]) keeps a real per-case subscription instead.

  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const signature = `${q}|${status}|${severity}`;
  const selection = useSelection();
  if (selection.signature !== signature) selection.setSignature(signature);

  // Cases settings (dispositions / case fields / SLA) — visible to anyone who
  // can manage at least one of those surfaces; the pages re-gate per action.
  const { can } = useCapabilities();
  const canSeeSettings =
    can(FEATURE_GATES.manageDispositions) ||
    can(FEATURE_GATES.updateDisposition) ||
    can(FEATURE_GATES.manageCaseFields) ||
    can(FEATURE_GATES.manageSlaPolicy);

  const columns: Column<Case>[] = [
    { id: "num", header: t("cases.number"), width: 90, cell: (c) => <span className="font-mono">#{c.caseNumber ?? "—"}</span> },
    { id: "title", header: "Title", cell: (c) => <span className="font-medium">{c.title ?? c.urn}</span> },
    { id: "severity", header: t("cases.severity"), width: 110, cell: (c) => <StatusChip status={c.severity} /> },
    { id: "status", header: t("cases.status"), width: 130, cell: (c) => <StatusChip status={c.status} live /> },
    { id: "assignee", header: t("cases.assignee"), width: 160, cell: (c) => c.assignee?.fullName ?? c.assignee?.email ?? "—" },
    { id: "due", header: t("cases.due"), width: 150, cell: (c) => formatLocal(c.dueDate) },
  ];

  return (
    <div>
      <PageHeader
        title={t("cases.title")}
        description="Claim triage over the full case index."
        actions={
          <div className="flex items-center gap-2">
            <CaseExportButton status={status || undefined} />
            {canSeeSettings && (
              <Button asChild size="sm" variant="ghost">
                <Link href="/cases/settings">
                  <Settings className="mr-1 size-3.5" aria-hidden />
                  Settings
                </Link>
              </Button>
            )}
          </div>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search cases…"
          defaultValue={q}
          onChange={(e) => setParam("q", e.target.value)}
          className="max-w-xs"
          aria-label="Search cases"
        />
        <Facet label="Status" value={status} options={STATUSES} onChange={(v) => setParam("status", v)} />
        <Facet label="Severity" value={severity} options={SEVERITIES} onChange={(v) => setParam("severity", v)} />
        {(q || status || severity) && (
          <Button variant="ghost" size="sm" onClick={() => router.replace("/cases")}>
            Clear
          </Button>
        )}
      </div>

      <BulkAssignBar caseCount={rows.length} />

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No cases match these filters"
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Cases"
          rows={rows}
          columns={columns}
          rowId={(c) => c.id}
          selectable
          selectedIds={selection.ids}
          onToggle={selection.toggle}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          onRowActivate={(c) => router.push(`/cases/${c.id}`)}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <Briefcase className="size-8" />
              <p>No cases</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}

function Facet({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 rounded-md border border-input bg-background px-2 text-sm"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o ? o.replaceAll("_", " ").toLowerCase() : "all"}
          </option>
        ))}
      </select>
    </label>
  );
}
