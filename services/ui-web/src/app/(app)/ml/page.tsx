"use client";
import { useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FlaskConical, Plus, Boxes, LineChart, GitCompareArrows } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { UrnLink } from "@/components/primitives/UrnLink";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES, cap } from "@/lib/authz/registry";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { useExperiments } from "@/lib/graphql/hooks";
import type { Experiment } from "@/lib/graphql/types";

const SUB_SECTIONS = [
  { href: "/ml/models", title: "Models", icon: Boxes, gate: cap("experiment.model.read") },
  { href: "/ml/inference", title: "Inference jobs", icon: LineChart, gate: cap("inference.job.read") },
  { href: "/ml/eval", title: "Eval", icon: GitCompareArrows, gate: cap("eval.run.read") },
];

function MlSubNav() {
  const { can } = useCapabilities();
  const visible = SUB_SECTIONS.filter((s) => can(s.gate));
  if (visible.length === 0) return null;
  return (
    <div className="mb-4 flex flex-wrap gap-2">
      {visible.map(({ href, title, icon: Icon }) => (
        <Link key={href} href={href}>
          <Button variant="outline" size="sm"><Icon className="size-3.5" /> {title}</Button>
        </Link>
      ))}
    </div>
  );
}

export default function MlExperimentsPage() {
  const router = useRouter();
  const query = useExperiments();
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);

  const newButton = (
    <Can gate={FEATURE_GATES.createExperiment}>
      <Button onClick={() => router.push("/ml/experiments/new")}>
        <Plus /> New experiment
      </Button>
    </Can>
  );

  const columns: Column<Experiment>[] = [
    { id: "name", header: "Name", cell: (e) => <span className="font-medium">{e.name}</span> },
    {
      id: "description",
      header: "Description",
      width: "2fr",
      cell: (e) => <span className="text-muted-foreground">{e.description ?? "—"}</span>,
    },
    { id: "urn", header: "URN", width: "1.5fr", cell: (e) => <UrnLink urn={e.urn} /> },
  ];

  return (
    <div>
      <PageHeader
        title="Experiments"
        description="Training experiments; rows open the runs table with live status."
        actions={newButton}
      />
      <MlSubNav />

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={rows.length === 0}
        emptyTitle="No experiments yet"
        emptyCta={newButton}
        onRetry={() => query.refetch()}
      >
        <DataTable
          ariaLabel="Experiments"
          rows={rows}
          columns={columns}
          rowId={(e) => e.id}
          hasMore={query.hasNextPage}
          isFetchingMore={query.isFetchingNextPage}
          onLoadMore={() => query.fetchNextPage()}
          onRowActivate={(e) => router.push(`/ml/experiments/${e.id}`)}
          emptyState={
            <div className="flex flex-col items-center gap-2 p-10 text-muted-foreground">
              <FlaskConical className="size-8" />
              <p>No experiments</p>
            </div>
          }
        />
      </AsyncBoundary>
    </div>
  );
}
