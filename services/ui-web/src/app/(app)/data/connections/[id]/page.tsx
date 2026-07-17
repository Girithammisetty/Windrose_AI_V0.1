"use client";
import { use, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Can } from "@/components/authz/Can";
import { Button } from "@/components/ui/button";
import { ConnectionForm } from "@/components/connections/ConnectionForm";
import { ConnectionPreviewPanel } from "@/components/connections/ConnectionPreviewPanel";
import { FEATURE_GATES } from "@/lib/authz/registry";
import { useConnection, useConnectorTypes } from "@/lib/graphql/hooks";
import { t } from "@/lib/i18n/messages";

/**
 * Edit a saved connection (ingestion-service PATCH /connections/{id}) + a live
 * sample-rows preview (POST .../preview). The form reuses the create flow's
 * schema-generated widgets; secrets are write-only — blank keeps the Vault
 * value, entered keys merge over it (US-6 partial rotation).
 */
export default function ConnectionEditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const detail = useConnection(id);
  const catalog = useConnectorTypes();
  const [banner, setBanner] = useState<string | null>(null);

  const conn = detail.data?.connection ?? null;
  const type = conn ? (catalog.data ?? []).find((x) => x.connectorType === conn.connectorType) : undefined;

  return (
    <div>
      <PageHeader
        title={conn ? `${t("connections.editTitle")} · ${conn.name}` : t("connections.editTitle")}
        description={type?.displayName}
        actions={
          <Button variant="ghost" size="sm" onClick={() => router.push("/data/connections")}>
            <ArrowLeft /> {t("connections.back")}
          </Button>
        }
      />

      {banner && (
        <div role="status" className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="notice-banner">
          {banner}
        </div>
      )}

      <AsyncBoundary
        isLoading={detail.isLoading || catalog.isLoading}
        isError={detail.isError || catalog.isError}
        error={(detail.error ?? catalog.error) as Error | null}
        isEmpty={!conn || !type}
        emptyTitle={t("connections.empty")}
        onRetry={() => {
          detail.refetch();
          catalog.refetch();
        }}
      >
        {conn && type && (
          <div className="space-y-6">
            <Can gate={FEATURE_GATES.updateConnection} fallback={null}>
              <ConnectionForm
                // Remount when a fresh connection arrives so the seeded values match.
                key={`${conn.id}:${conn.updatedAt ?? ""}`}
                type={type}
                editing={conn}
                onSaved={() => setBanner(t("connections.updated"))}
                onCancel={() => router.push("/data/connections")}
              />
            </Can>
            <Can gate={FEATURE_GATES.previewConnection}>
              <ConnectionPreviewPanel connectionId={conn.id} />
            </Can>
          </div>
        )}
      </AsyncBoundary>
    </div>
  );
}
