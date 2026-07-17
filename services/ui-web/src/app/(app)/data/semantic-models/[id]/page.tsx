"use client";
import { use, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import * as Tabs from "@radix-ui/react-tabs";
import { ArrowLeft, Loader2 } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { StatusChip } from "@/components/primitives/StatusChip";
import { Badge } from "@/components/ui/primitives";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useSemanticModelDetail,
  useSemanticModelVersions,
  useSemanticModelVersion,
  useCreateSemanticModelVersion,
} from "@/lib/graphql/hooks";
import { DefinitionEditor } from "@/components/semantic/DefinitionEditor";
import { ReviewActions } from "@/components/semantic/ReviewActions";
import { VersionsPanel } from "@/components/semantic/VersionsPanel";
import { CompilePreview } from "@/components/semantic/CompilePreview";
import { BootstrapButton } from "@/components/semantic/BootstrapButton";
import { normalizeDefinition } from "@/lib/semantic/definition";
import { t } from "@/lib/i18n/messages";

const OPEN_STATUSES = new Set(["DRAFT", "IN_REVIEW", "REJECTED"]);
const STATUS_TO_CHIP: Record<string, string> = {
  DRAFT: "DRAFT",
  IN_REVIEW: "PENDING",
  PUBLISHED: "SUCCEEDED",
  REJECTED: "FAILED",
  SUPERSEDED: "CANCELLED",
};

export default function SemanticModelDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [banner, setBanner] = useState<string | null>(null);

  const modelQuery = useSemanticModelDetail(id);
  const versionsQuery = useSemanticModelVersions(id);
  const versions = useMemo(() => versionsQuery.data?.pages.flatMap((p) => p.nodes) ?? [], [versionsQuery.data]);

  // The version to show in the Editor tab: the open one (draft/in_review/
  // rejected — server allows at most one) if it exists, else the published one.
  const targetVersionNo = useMemo(() => {
    const open = versions.find((v) => OPEN_STATUSES.has(v.status));
    if (open) return open.versionNo;
    const model = modelQuery.data;
    if (model?.publishedVersionNo != null) return model.publishedVersionNo;
    return versions[0]?.versionNo ?? null;
  }, [versions, modelQuery.data]);

  const versionQuery = useSemanticModelVersion(id, targetVersionNo);
  const createVersionMutation = useCreateSemanticModelVersion(id);

  const isLoading = modelQuery.isLoading || versionsQuery.isLoading || versionQuery.isLoading;
  const model = modelQuery.data;
  const version = versionQuery.data;

  return (
    <div>
      <AsyncBoundary
        isLoading={isLoading}
        isError={modelQuery.isError || versionsQuery.isError}
        error={modelQuery.error ?? versionsQuery.error}
        isEmpty={!isLoading && !model}
        emptyTitle={t("semantic.notFound")}
        onRetry={() => {
          modelQuery.refetch();
          versionsQuery.refetch();
        }}
      >
        {model && (
          <>
            <PageHeader
              title={model.name}
              description={model.description ?? undefined}
              actions={
                <>
                  {model.publishedVersionNo != null && (
                    <Badge variant="secondary">{t("semantic.published", { version: model.publishedVersionNo })}</Badge>
                  )}
                  <Button variant="ghost" size="sm" onClick={() => router.push("/data/semantic-models")}>
                    <ArrowLeft /> {t("semantic.back")}
                  </Button>
                </>
              }
            />

            <Tabs.Root defaultValue="editor">
              <Tabs.List className="mb-3 flex gap-1 border-b" aria-label="Semantic model sections">
                {(["editor", "versions", "preview"] as const).map((v) => (
                  <Tabs.Trigger
                    key={v}
                    value={v}
                    className="border-b-2 border-transparent px-3 py-2 text-sm font-medium text-muted-foreground data-[state=active]:border-primary data-[state=active]:text-foreground"
                  >
                    {t(`semantic.tab.${v}` as const)}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>

              <Tabs.Content value="editor">
                {version ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-2">
                      <StatusChip status={STATUS_TO_CHIP[version.status] ?? version.status} />
                      <span className="text-sm text-muted-foreground">v{version.versionNo}</span>
                      {/* Bootstrap rewrites the OPEN draft — only offer it there. */}
                      {version.status === "DRAFT" && (
                        <BootstrapButton
                          modelId={id}
                          onCompleted={() => versionQuery.refetch()}
                          onNotice={setBanner}
                        />
                      )}
                    </div>

                    {banner && (
                      <div role="status" className="rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="notice-banner">
                        {banner}
                      </div>
                    )}

                    {version.status === "IN_REVIEW" && (
                      <ReviewActions modelId={id} version={version} onDecided={() => versionQuery.refetch()} />
                    )}

                    {!OPEN_STATUSES.has(version.status) && (
                      <Can gate={FEATURE_GATES.updateSemanticModel}>
                        <Button
                          variant="outline"
                          onClick={() =>
                            createVersionMutation.mutate(undefined, {
                              onSuccess: () => {
                                versionsQuery.refetch();
                                versionQuery.refetch();
                              },
                            })
                          }
                          disabled={createVersionMutation.isPending}
                        >
                          {createVersionMutation.isPending ? <Loader2 className="animate-spin" /> : null}{" "}
                          {createVersionMutation.isPending ? t("semantic.openingDraft") : t("semantic.openDraft")}
                        </Button>
                      </Can>
                    )}

                    <DefinitionEditor modelId={id} version={version} onSubmitted={() => versionQuery.refetch()} />
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">{t("semantic.notFound")}</p>
                )}
              </Tabs.Content>

              <Tabs.Content value="versions">
                <VersionsPanel modelId={id} />
              </Tabs.Content>

              <Tabs.Content value="preview">
                <CompilePreview
                  modelId={id}
                  doc={normalizeDefinition(version?.definitionJson)}
                  draftVersionNo={version && version.status !== "PUBLISHED" ? version.versionNo : undefined}
                />
              </Tabs.Content>
            </Tabs.Root>
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}
