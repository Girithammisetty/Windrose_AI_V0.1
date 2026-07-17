"use client";
import { use } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Button } from "@/components/ui/button";
import { PipelineBuilder } from "@/components/pipelines/PipelineBuilder";
import { usePipelineStepTypes, usePipelineTemplate } from "@/lib/graphql/hooks";
import { t } from "@/lib/i18n/messages";

/** Edit an existing pipeline template: the builder rehydrates the canvas from the
 * template's saved definition and Save becomes an Update (new version). */
export default function EditPipelinePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const steps = usePipelineStepTypes();
  const template = usePipelineTemplate(id);

  const tpl = template.data?.pipelineTemplate ?? null;
  const isLoading = steps.isLoading || template.isLoading;
  const isError = steps.isError || template.isError;
  const error = steps.error ?? template.error;

  return (
    <div>
      <PageHeader
        title={t("pipelines.editTitle")}
        description={t("pipelines.editHint")}
        actions={
          <Button variant="ghost" size="sm" onClick={() => router.push("/data/pipelines")}>
            <ArrowLeft /> {t("pipelines.back")}
          </Button>
        }
      />

      <AsyncBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={!tpl || (steps.data?.length ?? 0) === 0}
        emptyTitle={t("pipelines.noSteps")}
        onRetry={() => {
          steps.refetch();
          template.refetch();
        }}
      >
        {tpl && <PipelineBuilder steps={steps.data ?? []} editTemplate={tpl} onSaved={() => router.push("/data/pipelines")} />}
      </AsyncBoundary>
    </div>
  );
}
