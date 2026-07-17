"use client";
import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { ConfirmDialog } from "@/components/primitives/ConfirmDialog";
import { StatusChip } from "@/components/primitives/StatusChip";
import { UrnLink } from "@/components/primitives/UrnLink";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useInferenceJob,
  // Tier 4b: ml ops — job lifecycle.
  useCancelInferenceJob,
  useRetryInferenceJob,
  useDeleteInferenceJob,
} from "@/lib/graphql/hooks";
import { useHubTopics } from "@/lib/realtime/useHubTopics";
import { formatLocal } from "@/lib/utils";
import { inferenceStatusUi } from "@/lib/inference-status";

// inference-service state machine (domain/enums.py): CANCELLABLE / TERMINAL_FAILURE / TERMINAL.
const CANCELLABLE = new Set(["queued", "submitted", "running"]);
const RETRYABLE = new Set(["rejected", "failed", "cancelled"]);
const TERMINAL = new Set(["rejected", "succeeded", "failed", "cancelled"]);

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate text-right font-medium">{children}</span>
    </div>
  );
}

export default function InferenceJobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const query = useInferenceJob(id);
  const job = query.data?.inferenceJob;
  // Task #78: "inference.status" wasn't a valid topic — real subscription is
  // run-status:<job-urn> (inference-service's events carry resource_urn =
  // the job's own URN; see routing.go's "inference" rule, prefix "inference.job.").
  useHubTopics(job?.urn ? [`run-status:${job.urn}`] : []);

  const cancel = useCancelInferenceJob();
  const retry = useRetryInferenceJob();
  const remove = useDeleteInferenceJob();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const actionError = (cancel.error ?? retry.error ?? remove.error) as Error | null;

  const status = job?.status ?? "";

  return (
    <div>
      <AsyncBoundary
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={!query.isLoading && !job}
        emptyTitle="Inference job not found"
        onRetry={() => query.refetch()}
      >
        {job && (
          <>
            <PageHeader
              title={job.name ?? job.id}
              description={job.description ?? job.urn}
              actions={
                <div className="flex items-center gap-2">
                  {CANCELLABLE.has(status) && (
                    <Can gate={FEATURE_GATES.cancelInferenceJob}>
                      <Button variant="outline" size="sm" disabled={cancel.isPending} onClick={() => setConfirmCancel(true)}>
                        Cancel job
                      </Button>
                    </Can>
                  )}
                  {RETRYABLE.has(status) && (
                    <Can gate={FEATURE_GATES.createInferenceJob}>
                      <Button
                        size="sm"
                        disabled={retry.isPending}
                        onClick={() =>
                          retry.mutate(id, {
                            // The result is the NEW job — navigate to it.
                            onSuccess: (newJob) => router.push(`/ml/inference/${newJob.id}`),
                          })
                        }
                      >
                        {retry.isPending ? "Retrying…" : "Retry"}
                      </Button>
                    </Can>
                  )}
                  {TERMINAL.has(status) && (
                    <Can gate={FEATURE_GATES.deleteInferenceJob}>
                      <Button variant="destructive" size="sm" disabled={remove.isPending} onClick={() => setConfirmDelete(true)}>
                        Delete
                      </Button>
                    </Can>
                  )}
                  <StatusChip status={inferenceStatusUi(job.status)} live />
                </div>
              }
            />

            {actionError && (
              <p role="alert" className="mb-3 text-xs text-destructive" data-testid="mutation-error">
                {actionError.message}
              </p>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Model + data</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <Row label="Model">
                    {job.model?.urn ? <UrnLink urn={job.model.urn} label={job.model.name ?? undefined} /> : (job.model?.name ?? "—")}
                  </Row>
                  <Row label="Version">{job.model?.version != null ? `v${job.model.version}` : "—"}</Row>
                  <Row label="Stage at submit">{job.model?.stageAtSubmit ?? "—"}</Row>
                  <Row label="Input dataset">
                    {job.inputDataset?.urn ? <UrnLink urn={job.inputDataset.urn} /> : "—"}
                  </Row>
                  <Row label="Output dataset">
                    {job.outputDataset?.urn ? <UrnLink urn={job.outputDataset.urn} /> : "—"}
                  </Row>
                  <Row label="Rows scored">{job.rowCount != null ? job.rowCount.toLocaleString() : "—"}</Row>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Timeline</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <Row label="Created">{formatLocal(job.createdAt)}</Row>
                  <Row label="Submitted">{formatLocal(job.submittedAt)}</Row>
                  <Row label="Started">{formatLocal(job.startedAt)}</Row>
                  <Row label="Finished">{formatLocal(job.finishedAt)}</Row>
                  {job.pipelineRunUrn && (
                    <Row label="Pipeline run">
                      <span className="font-mono text-xs">{job.pipelineRunUrn}</span>
                    </Row>
                  )}
                  {job.retriedFromJobId && (
                    <Row label="Retried from">
                      <Link href={`/ml/inference/${job.retriedFromJobId}`} className="font-mono text-xs text-primary hover:underline">
                        {job.retriedFromJobId}
                      </Link>
                    </Row>
                  )}
                </CardContent>
              </Card>
            </div>

            {job.error && (
              <div
                role="alert"
                className="mt-4 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
              >
                {job.error}
              </div>
            )}

            <ConfirmDialog
              open={confirmCancel}
              onOpenChange={setConfirmCancel}
              title="Cancel inference job"
              description="Stop this job? A running job transitions through cancelling before it terminates."
              confirmLabel="Cancel job"
              onConfirm={() => {
                cancel.mutate(id, { onSettled: () => setConfirmCancel(false) });
              }}
            />

            <ConfirmDialog
              open={confirmDelete}
              onOpenChange={setConfirmDelete}
              title="Delete inference job"
              description="Delete this terminal job? Its row is removed from the job list."
              confirmLabel="Delete"
              destructive
              onConfirm={() => {
                remove.mutate(id, {
                  onSuccess: () => router.push("/ml/inference"),
                  onSettled: () => setConfirmDelete(false),
                });
              }}
            />
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}
