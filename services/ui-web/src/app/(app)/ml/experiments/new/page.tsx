"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { usePipelineTemplates, useCreateExperiment } from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { PipelineTemplate } from "@/lib/graphql/types";

const MODEL_TYPES = [
  "classification",
  "regression",
  "anomaly_detection",
  "forecasting",
  "unsupervised",
  "clustering",
] as const;

function PipelineSelect({
  id,
  label,
  value,
  onChange,
  pipelines,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  pipelines: PipelineTemplate[];
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        className="h-9 w-full rounded-md border bg-background px-3 text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Select a pipeline…</option>
        {pipelines.map((p) => (
          <option key={p.id} value={p.urn}>
            {p.name} ({p.pipelineType})
          </option>
        ))}
      </select>
    </div>
  );
}

export default function NewExperimentPage() {
  const router = useRouter();
  const { can, isReady } = useCapabilities();
  const allowed = can(FEATURE_GATES.createExperiment);

  const pipelinesQuery = usePipelineTemplates();
  const pipelines = useMemo(
    () => pipelinesQuery.data?.pages.flatMap((p) => p.nodes) ?? [],
    [pipelinesQuery.data],
  );

  const [name, setName] = useState("");
  const [modelType, setModelType] = useState<string>("classification");
  const [description, setDescription] = useState("");
  const [modelPipelineUrn, setModelPipelineUrn] = useState("");
  const [fePipelineUrn, setFePipelineUrn] = useState("");
  const [trainPipelineUrn, setTrainPipelineUrn] = useState("");

  const create = useCreateExperiment();
  const error = create.error instanceof GraphQLRequestError ? create.error : null;

  const urns = [modelPipelineUrn, fePipelineUrn, trainPipelineUrn];
  const allChosen = urns.every(Boolean);
  const distinct = new Set(urns).size === 3;
  const canSubmit = !!name.trim() && allChosen && distinct && !create.isPending;

  const submit = () => {
    if (!canSubmit) return;
    create.mutate(
      {
        name: name.trim(),
        modelType,
        description: description.trim() || undefined,
        modelPipelineUrn,
        featureEngineeringPipelineUrn: fePipelineUrn,
        trainingPipelineUrn: trainPipelineUrn,
      },
      { onSuccess: (exp) => router.push(`/ml/experiments/${exp.id}`) },
    );
  };

  if (isReady && !allowed) {
    return (
      <div>
        <PageHeader title="New experiment" />
        <div role="alert" data-testid="no-access" className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">
          You don&apos;t have access to create experiments. Ask an admin if you need it.
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="New experiment"
        description="An experiment binds three pipelines (feature-engineering, model, training). The workspace comes from your session."
      />

      <AsyncBoundary
        isLoading={pipelinesQuery.isLoading}
        isError={pipelinesQuery.isError}
        error={pipelinesQuery.error}
        isEmpty={pipelines.length < 3}
        emptyTitle="At least three pipelines are required to create an experiment."
        onRetry={() => pipelinesQuery.refetch()}
      >
        <Card>
          <CardContent className="space-y-4 pt-5">
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
            >
              <div className="space-y-1.5">
                <Label htmlFor="exp-name">Name</Label>
                <Input id="exp-name" value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="Claims fraud v3" />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="model-type">Model type</Label>
                <select
                  id="model-type"
                  className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                  value={modelType}
                  onChange={(e) => setModelType(e.target.value)}
                >
                  {MODEL_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>

              <PipelineSelect id="fe-pipe" label="Feature-engineering pipeline" value={fePipelineUrn} onChange={setFePipelineUrn} pipelines={pipelines} />
              <PipelineSelect id="model-pipe" label="Model pipeline" value={modelPipelineUrn} onChange={setModelPipelineUrn} pipelines={pipelines} />
              <PipelineSelect id="train-pipe" label="Training pipeline" value={trainPipelineUrn} onChange={setTrainPipelineUrn} pipelines={pipelines} />
              {allChosen && !distinct && (
                <p className="text-xs text-destructive">The three pipelines must be distinct.</p>
              )}

              <div className="space-y-1.5">
                <Label htmlFor="exp-desc">Description (optional)</Label>
                <Input id="exp-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
              </div>

              {error && (
                <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                  {error.message}
                  {error.traceId ? ` (trace: ${error.traceId})` : ""}
                </p>
              )}

              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => router.push("/ml")}>
                  Cancel
                </Button>
                <Button type="submit" disabled={!canSubmit}>
                  {create.isPending ? "Creating…" : "Create experiment"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </AsyncBoundary>
    </div>
  );
}
