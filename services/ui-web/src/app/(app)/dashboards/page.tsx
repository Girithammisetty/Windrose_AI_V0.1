"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LayoutDashboard, Plus } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Card, CardHeader, CardTitle, Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { CreateDashboardDialog } from "@/components/charts/CreateDashboardDialog";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useDashboards } from "@/lib/graphql/hooks";
import { useSession } from "@/lib/session/SessionContext";
import { t } from "@/lib/i18n/messages";

export default function DashboardsPage() {
  const { workspaceId } = useSession();
  const router = useRouter();
  const query = useDashboards(workspaceId);
  const items = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const [creating, setCreating] = useState(false);

  const onCreated = (id: string) => {
    setCreating(false);
    router.push(`/dashboards/${id}`);
  };

  return (
    <div>
      <PageHeader
        title={t("dashboards.title")}
        description={t("dashboards.subtitle")}
        actions={
          <Can gate={FEATURE_GATES.createDashboard}>
            <Button onClick={() => setCreating(true)}>
              <Plus /> {t("dashboards.create")}
            </Button>
          </Can>
        }
      />

      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={items.length === 0}
        emptyTitle={t("dashboards.empty")}
        emptyCta={
          <Can gate={FEATURE_GATES.createDashboard}>
            <Button className="mt-2" onClick={() => setCreating(true)}>
              <Plus /> {t("dashboards.create")}
            </Button>
          </Can>
        }
        onRetry={() => query.refetch()}
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((d) => (
            <Link key={d.id} href={`/dashboards/${d.id}`} className="focus-visible:outline-none">
              <Card className="h-full transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-primary">
                <CardHeader>
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <LayoutDashboard className="size-4" aria-hidden />
                    {d.module && <Badge variant="secondary">{d.module}</Badge>}
                  </div>
                  <CardTitle className="text-base">{d.title}</CardTitle>
                </CardHeader>
              </Card>
            </Link>
          ))}
        </div>
      </AsyncBoundary>

      <CreateDashboardDialog open={creating} onOpenChange={setCreating} onCreated={onCreated} />
    </div>
  );
}
