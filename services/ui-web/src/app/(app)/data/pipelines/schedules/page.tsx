"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { PipelineSchedulesPanel } from "@/components/pipelines/PipelineSchedules";
import { t } from "@/lib/i18n/messages";

/**
 * Recurring pipeline schedules (pipeline-orchestrator /pipeline-schedules).
 * Mirrors the ingestion Schedules tab: list schedules, create one, and
 * pause/resume/run-now/delete per row.
 */
export default function PipelineSchedulesPage() {
  const router = useRouter();
  const [banner, setBanner] = useState<string | null>(null);

  return (
    <div>
      <PageHeader
        title={t("pipelines.schedules.title")}
        description={t("pipelines.schedules.subtitle")}
        actions={
          <Button variant="outline" onClick={() => router.push("/data/pipelines")}>
            <ArrowLeft /> {t("pipelines.back")}
          </Button>
        }
      />

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="notice-banner">
          {banner}
        </div>
      )}

      <PipelineSchedulesPanel onNotice={setBanner} />
    </div>
  );
}
