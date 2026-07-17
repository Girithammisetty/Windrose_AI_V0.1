"use client";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Button } from "@/components/ui/button";
import { PipelineBuilder } from "@/components/pipelines/PipelineBuilder";
import { usePipelineStepTypes } from "@/lib/graphql/hooks";
import { t } from "@/lib/i18n/messages";

export default function NewPipelinePage() {
  const router = useRouter();
  const steps = usePipelineStepTypes();

  const isLoading = steps.isLoading;
  const isError = steps.isError;
  const error = steps.error;

  return (
    <div>
      <PageHeader
        title={t("pipelines.builderTitle")}
        description={t("pipelines.builderHint")}
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
        isEmpty={(steps.data?.length ?? 0) === 0}
        emptyTitle={t("pipelines.noSteps")}
        onRetry={() => steps.refetch()}
      >
        <PipelineBuilder steps={steps.data ?? []} />
      </AsyncBoundary>
    </div>
  );
}
