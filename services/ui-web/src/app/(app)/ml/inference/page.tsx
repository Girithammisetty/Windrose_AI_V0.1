"use client";
import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { inferenceStatusUi } from "@/lib/inference-status";
import * as Tabs from "@radix-ui/react-tabs";
import { Bot, Plus } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
// Tier 4b: ml ops — recurring scoring schedules.
import { InferenceSchedulesPanel } from "@/components/ml/InferenceSchedulesPanel";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { useInferenceJobs } from "@/lib/graphql/hooks";
import { formatLocal } from "@/lib/utils";
import type { InferenceJob } from "@/lib/graphql/types";

export default function MlInferencePage() {
  const router = useRouter();
  const query = useInferenceJobs();
  // Task #78: list-wide "inference.status" isn't a valid topic (grammar is
  // scheme:identifier, and there's no "all jobs" broadcast scheme). Removed;
  // the detail page (ml/inference/[id]) keeps a real per-job subscription.
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  // Tier 4b: ml ops — the Schedules tab appears only for schedule readers.
  const { can } = useCapabilities();
  const canSeeSchedules = can(FEATURE_GATES.readInferenceSchedules);

  const newButton = (
    <Can gate={FEATURE_GATES.createInferenceJob}>
      <Button onClick={() => router.push("/ml/inference/new")}>
        <Plus /> New job
      </Button>
    </Can>
  );

  const columns: Column<InferenceJob>[] = [
    { id: "name", header: "Job", width: "1.5fr", cell: (j) => <span className="font-medium">{j.name ?? j.id}</span> },
    { id: "status", header: "Status", width: 140, cell: (j) => <StatusChip status={inferenceStatusUi(j.status)} live /> },
    {
      id: "model", header: "Model", width: "1.5fr",
      cell: (j) => (
        <span className="truncate">
          {j.model?.name ?? "—"}
          {j.model?.version != null && <span className="ml-1 font-mono text-xs text-muted-foreground">v{j.model.version}</span>}
        </span>
      ),
    },
    { id: "rows", header: "Rows", width: 90, cell: (j) => (j.rowCount != null ? j.rowCount.toLocaleString() : "—") },
    { id: "created", header: "Created", width: 170, cell: (j) => formatLocal(j.createdAt) },
  ];

  const jobsTable = (
    <AsyncBoundary
      isLoading={query.isLoading}
      isError={query.isError}
      error={query.error}
      isEmpty={rows.length === 0}
      emptyTitle="No inference jobs yet."
      emptyCta={newButton}
      onRetry={() => query.refetch()}
    >
      <DataTable
        ariaLabel="Inference jobs"
        rows={rows}
        columns={columns}
        rowId={(j) => j.id}
        hasMore={query.hasNextPage}
        isFetchingMore={query.isFetchingNextPage}
        onLoadMore={() => query.fetchNextPage()}
        onRowActivate={(j) => router.push(`/ml/inference/${j.id}`)}
        emptyState={
          <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
            <Bot className="size-8" />
            <p>No inference jobs</p>
          </div>
        }
      />
    </AsyncBoundary>
  );

  return (
    <div>
      <PageHeader
        title="Inference jobs"
        description="Batch scoring jobs over a promoted model and an input dataset."
        actions={newButton}
      />

      {canSeeSchedules ? (
        <Tabs.Root defaultValue="jobs">
          <Tabs.List className="mb-3 flex gap-1 border-b" aria-label="Inference sections">
            {(["jobs", "schedules"] as const).map((v) => (
              <Tabs.Trigger
                key={v}
                value={v}
                className="border-b-2 border-transparent px-3 py-2 text-sm font-medium capitalize text-muted-foreground data-[state=active]:border-primary data-[state=active]:text-foreground"
              >
                {v}
              </Tabs.Trigger>
            ))}
          </Tabs.List>
          <Tabs.Content value="jobs">{jobsTable}</Tabs.Content>
          <Tabs.Content value="schedules">
            <InferenceSchedulesPanel />
          </Tabs.Content>
        </Tabs.Root>
      ) : (
        jobsTable
      )}
    </div>
  );
}
