"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Badge, Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useModels, useModel, useDatasets, useCreateInferenceJob,
  // Tier 4b: ml ops — compatibility preflight.
  useValidateInference,
} from "@/lib/graphql/hooks";
import { GraphQLRequestError } from "@/lib/graphql/client";
import type { InferenceCompatibilityReport } from "@/lib/graphql/types";

export default function NewInferenceJobPage() {
  const router = useRouter();
  const { can, isReady } = useCapabilities();
  const allowed = can(FEATURE_GATES.createInferenceJob);

  const modelsQuery = useModels();
  const models = useMemo(() => modelsQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [modelsQuery.data]);
  const datasetsQuery = useDatasets();
  const datasets = useMemo(() => datasetsQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [datasetsQuery.data]);

  const [modelId, setModelId] = useState("");
  const [version, setVersion] = useState<string>("");
  const [datasetUrn, setDatasetUrn] = useState("");
  const [name, setName] = useState("");

  const modelDetail = useModel(modelId);
  const versions = useMemo(() => modelDetail.data?.model?.versions ?? [], [modelDetail.data]);
  const selectedVersion = versions.find((v) => String(v.version) === version);
  const allowUnpromoted = !!selectedVersion && selectedVersion.stage !== "production";

  const create = useCreateInferenceJob();
  const error = create.error instanceof GraphQLRequestError ? create.error : null;

  // Tier 4b: ml ops — read-only compatibility preflight (the submit
  // re-validates server-side regardless; this never blocks submit).
  const validate = useValidateInference();
  const validateError = validate.error as Error | null;

  // Default the version select to the production version once versions load.
  useEffect(() => {
    if (modelId && versions.length > 0 && !version) {
      const prod = versions.find((v) => v.stage === "production");
      setVersion(String((prod ?? versions[0]).version));
    }
  }, [modelId, versions, version]);

  const canSubmit = !!selectedVersion?.urn && !!datasetUrn && !create.isPending;

  const submit = () => {
    if (!selectedVersion?.urn || !datasetUrn) return;
    create.mutate(
      {
        modelVersionUrn: selectedVersion.urn,
        inputDatasetUrn: datasetUrn,
        name: name.trim() || undefined,
        allowUnpromoted,
      },
      { onSuccess: (job) => router.push(`/ml/inference/${job.id}`) },
    );
  };

  if (isReady && !allowed) {
    return (
      <div>
        <PageHeader title="New inference job" />
        <div role="alert" data-testid="no-access" className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">
          You don&apos;t have access to submit prediction jobs. Ask an admin if you need it.
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="New inference job" description="Score an input dataset with a registered model version." />

      <AsyncBoundary
        isLoading={modelsQuery.isLoading || datasetsQuery.isLoading}
        isError={modelsQuery.isError || datasetsQuery.isError}
        error={modelsQuery.error ?? datasetsQuery.error}
        isEmpty={models.length === 0}
        emptyTitle="No registered models to score with yet."
        onRetry={() => {
          modelsQuery.refetch();
          datasetsQuery.refetch();
        }}
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
                <Label htmlFor="job-name">Job name (optional)</Label>
                <Input id="job-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nightly claims scoring" />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="model">Model</Label>
                <select
                  id="model"
                  className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                  value={modelId}
                  onChange={(e) => {
                    setModelId(e.target.value);
                    setVersion("");
                  }}
                >
                  <option value="">Select a model…</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name ?? m.id}
                      {m.modelType ? ` (${m.modelType})` : ""}
                    </option>
                  ))}
                </select>
              </div>

              {modelId && (
                <div className="space-y-1.5">
                  <Label htmlFor="version">Version</Label>
                  <select
                    id="version"
                    className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                    value={version}
                    onChange={(e) => setVersion(e.target.value)}
                    disabled={modelDetail.isLoading}
                  >
                    {versions.length === 0 && <option value="">No versions</option>}
                    {versions.map((v) => (
                      <option key={v.version} value={String(v.version)}>
                        v{v.version} — {v.stage ?? "none"}
                      </option>
                    ))}
                  </select>
                  {allowUnpromoted && (
                    <p className="text-xs text-[hsl(var(--warning))]">
                      This version is not in production — the job will be submitted with allow_unpromoted.
                    </p>
                  )}
                </div>
              )}

              <div className="space-y-1.5">
                <Label htmlFor="dataset">Input dataset</Label>
                <select
                  id="dataset"
                  className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                  value={datasetUrn}
                  onChange={(e) => setDatasetUrn(e.target.value)}
                >
                  <option value="">Select a dataset…</option>
                  {datasets.map((d) => (
                    <option key={d.id} value={d.urn}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={!selectedVersion?.urn || !datasetUrn || validate.isPending}
                  onClick={() => {
                    if (!selectedVersion?.urn || !datasetUrn) return;
                    validate.mutate({
                      modelVersionUrn: selectedVersion.urn,
                      inputDatasetUrn: datasetUrn,
                      allowUnpromoted,
                    });
                  }}
                >
                  {validate.isPending ? "Validating…" : "Validate compatibility"}
                </Button>
                {validateError && (
                  <p role="alert" className="text-xs text-destructive" data-testid="validate-error">
                    {validateError.message}
                  </p>
                )}
                {validate.data && <CompatibilityReportView report={validate.data} />}
              </div>

              {error && (
                <p role="alert" className="text-xs text-destructive" data-testid="mutation-error">
                  {error.message}
                  {error.traceId ? ` (trace: ${error.traceId})` : ""}
                </p>
              )}

              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => router.push("/ml/inference")}>
                  Cancel
                </Button>
                <Button type="submit" disabled={!canSubmit}>
                  {create.isPending ? "Submitting…" : "Submit job"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </AsyncBoundary>
    </div>
  );
}

/** The REAL compatibility report from inference-service /inferences/validate:
 * verdict badge, per-column verdicts, warnings and any stage-policy error.
 * Informational only — the server re-validates at submit. */
function CompatibilityReportView({ report }: { report: InferenceCompatibilityReport }) {
  const warnings = Array.isArray(report.warnings) ? report.warnings : [];
  return (
    <div className="rounded-md border p-3 text-sm" data-testid="compat-report">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={report.compatible ? "success" : "destructive"}>
          {report.compatible ? "compatible" : "incompatible"}
        </Badge>
        {report.modelStage && <span className="text-xs text-muted-foreground">model stage: {report.modelStage}</span>}
        {report.rowCount != null && (
          <span className="text-xs tabular-nums text-muted-foreground">
            {report.rowCount.toLocaleString()} input rows
          </span>
        )}
      </div>
      {report.stageError && (
        <p role="alert" className="mt-2 text-xs text-destructive">
          Stage policy: {report.stageError}
        </p>
      )}
      {report.columns.length > 0 && (
        <table className="mt-2 w-full text-xs">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="py-1 pr-3 font-medium">Column</th>
              <th className="py-1 pr-3 font-medium">Required</th>
              <th className="py-1 pr-3 font-medium">Actual</th>
              <th className="py-1 font-medium">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {report.columns.map((c) => (
              <tr key={c.name} className="border-b last:border-0">
                <td className="py-1 pr-3 font-mono">{c.name}</td>
                <td className="py-1 pr-3">{c.requiredType ?? "—"}</td>
                <td className="py-1 pr-3">{c.actualType ?? "—"}</td>
                <td className={`py-1 ${c.verdict === "ok" ? "text-[hsl(var(--success))]" : "font-medium text-destructive"}`}>
                  {c.verdict}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {warnings.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-[hsl(var(--warning))]">
          {warnings.map((w, i) => (
            <li key={i}>{typeof w === "string" ? w : JSON.stringify(w)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
