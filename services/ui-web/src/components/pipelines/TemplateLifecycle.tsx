"use client";
import { useMemo } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Badge } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Can } from "@/components/authz/Can";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  usePipelineTemplateVersions,
  useActivatePipelineTemplateVersion,
} from "@/lib/graphql/hooks";
import type { PipelineTemplate, CompiledPipelineManifest } from "@/lib/graphql/types";
import { formatLocal } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";

/**
 * Immutable version history for one template (pipeline-orchestrator GET
 * /pipelines/{id}/versions) with per-version Activate (POST
 * .../versions/{v}/activate). Only "valid" versions can back a run.
 */
export function TemplateVersionsDialog({
  template,
  onOpenChange,
  onNotice,
}: {
  template: PipelineTemplate | null;
  onOpenChange: (o: boolean) => void;
  onNotice: (msg: string) => void;
}) {
  const query = usePipelineTemplateVersions(template?.id ?? "", !!template);
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.nodes) ?? [], [query.data]);
  const activateMutation = useActivatePipelineTemplateVersion();

  return (
    <Dialog.Root open={!!template} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">
            {t("pipelines.versionsTitle")}
            {template && <span className="ml-2 text-sm font-normal text-muted-foreground">{template.name}</span>}
          </Dialog.Title>
          <div className="mt-4">
            <AsyncBoundary
              isLoading={query.isLoading}
              isError={query.isError}
              error={query.error}
              isEmpty={rows.length === 0}
              emptyTitle={t("pipelines.versionsEmpty")}
              onRetry={() => query.refetch()}
            >
              <ul className="space-y-2" aria-label={t("pipelines.versionsTitle")}>
                {rows.map((v) => {
                  const isActive = template?.activeVersionId === v.id;
                  return (
                    <li key={v.id} className="flex items-center justify-between rounded-md border p-3 text-sm">
                      <div className="flex items-center gap-2">
                        <Badge variant={isActive ? "default" : "secondary"}>v{v.versionNo}</Badge>
                        {isActive && <Badge variant="success">{t("pipelines.versionActive")}</Badge>}
                        <StatusChip status={v.validationStatus === "valid" ? "SUCCEEDED" : "DRAFT"} />
                        <span className="text-xs text-muted-foreground">{formatLocal(v.createdAt)}</span>
                        {v.manifestDigest && (
                          <span className="font-mono text-[10px] text-muted-foreground">
                            {v.manifestDigest.slice(0, 12)}
                          </span>
                        )}
                      </div>
                      {!isActive && (
                        <Can gate={FEATURE_GATES.updatePipelineTemplate}>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={activateMutation.isPending}
                            onClick={() =>
                              activateMutation.mutate(
                                { templateId: v.templateId, versionId: v.id },
                                {
                                  onSuccess: () => onNotice(t("pipelines.versionActivated")),
                                  onError: (e) => onNotice((e as Error).message),
                                },
                              )
                            }
                          >
                            {t("pipelines.versionActivate")}
                          </Button>
                        </Can>
                      )}
                    </li>
                  );
                })}
              </ul>
              {query.hasNextPage && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-2"
                  disabled={query.isFetchingNextPage}
                  onClick={() => query.fetchNextPage()}
                >
                  {t("action.loadMore")}
                </Button>
              )}
            </AsyncBoundary>
          </div>
          <div className="mt-4 flex justify-end">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t("semantic.version.close")}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Compiled Argo manifest view (POST /pipelines/{id}/compile result). */
export function CompiledManifestDialog({
  result,
  isPending,
  error,
  open,
  onOpenChange,
}: {
  result: CompiledPipelineManifest | null;
  isPending: boolean;
  error: Error | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-full max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-card p-5 shadow-lg focus:outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="text-lg font-semibold">{t("pipelines.compiledTitle")}</Dialog.Title>
          <div className="mt-4 space-y-3">
            {isPending && <p className="text-sm text-muted-foreground">{t("state.loading")}</p>}
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error.message}
              </p>
            )}
            {result && (
              <>
                <p className="text-sm">
                  {result.manifestDigest && (
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      {result.manifestDigest}
                    </Badge>
                  )}
                  {result.argoTemplateName && (
                    <span className="ml-2 font-mono text-xs text-muted-foreground">{result.argoTemplateName}</span>
                  )}
                </p>
                <pre className="max-h-96 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs" data-testid="compiled-manifest">
                  {JSON.stringify(result.manifest ?? null, null, 2)}
                </pre>
              </>
            )}
          </div>
          <div className="mt-4 flex justify-end">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t("semantic.version.close")}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
